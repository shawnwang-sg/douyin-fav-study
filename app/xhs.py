"""小红书相关：解析分享链接、读收藏 / 专辑列表（浏览器登录态）、读笔记详情并下载（XHS-Downloader）。

接口和原来的 douyin.py 保持一致（Browser.sync_list / list_folders / sync_folder / fetch_detail / download ...），
worker 和 server 基本不用改。分工：
- 收藏列表、专辑：打开一个真实浏览器窗口（扫码登录一次，登录态存在 data/browser），
  进「我 → 收藏」页面，网页自己去请求列表，程序只「旁听」返回的数据，不自己伪造签名。
- 单条笔记的详情和无水印地址：交给 XHS-Downloader（https://github.com/JoeanAmier/XHS-Downloader），
  它在自己的虚拟环境里作为一个常驻小进程运行（app/xhs_bridge.py），不需要打开浏览器。
- 图片、视频文件：普通 HTTP 下载。
"""
import json
import os
import random
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config
from .config import XHS_BROWSER_DIR as BROWSER_DIR, DATA
from .douyin import DouyinError, NotLoggedIn, pw_acquire, pw_release  # 和抖音共用一套错误类型，worker 统一处理

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/146.0.0.0 Safari/537.36")
REFERER = "https://www.xiaohongshu.com/"
HOSTS = ("xiaohongshu.com", "xhslink.com", "rednote.com", "xhscdn.com")
URL_RE = re.compile(r"https?://[^\s　-〿＀-￯<>\"']+")
NOTE_ID = r"([0-9a-f]{24})"
ID_PATTERNS = [
    re.compile(r"/explore/" + NOTE_ID),
    re.compile(r"/discovery/item/" + NOTE_ID),
    re.compile(r"/user/profile/[0-9a-f]{24}/" + NOTE_ID),
    re.compile(r"[?&]noteId=" + NOTE_ID),
]
# XHS-Downloader 放在哪：环境变量 XHS_DOWNLOADER_DIR > 设置 xhs_downloader_dir > 程序目录\tools\XHS-Downloader > 用户目录\XHS-Downloader
XHS_DIR = Path(os.environ.get("XHS_DOWNLOADER_DIR") or config.load().get("xhs_downloader_dir")
               or next((p for p in (config.TOOLS_DIR / "XHS-Downloader",) if p.exists()), None)
               or Path.home() / "XHS-Downloader")
COOKIE_PATH = DATA / "xhs_cookie.txt"   # 登录后存一份 xiaohongshu.com 的 Cookie 给 XHS-Downloader 用

_URLS = {}  # 笔记 ID → 带 xsec_token 的完整链接（收藏列表里拿到的；没有 token 有些笔记打不开）


XhsError = DouyinError


class Cooldown(DouyinError):
    """防封号：被限流 / 要验证 / 今天读够了，先歇着。"""


# ---------------------------------------------------------------- 防封号守卫
# 记在 data/xhs_guard.json，程序重启也不会忘：冷却到什么时候、今天已经读了几条详情。
GUARD_PATH = DATA / "xhs_guard.json"
_guard_lock = threading.Lock()
_last_hit = [0.0]


def _guard():
    try:
        return json.loads(GUARD_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _guard_save(g):
    GUARD_PATH.write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")


def cooldown(minutes, why):
    with _guard_lock:
        g = _guard()
        g["cool_until"] = max(float(g.get("cool_until") or 0), time.time() + minutes * 60)
        g["cool_why"] = why
        _guard_save(g)


def guard_status():
    g = _guard()
    left = float(g.get("cool_until") or 0) - time.time()
    today = time.strftime("%Y-%m-%d")
    return {"cool_left": max(0, int(left)), "cool_why": g.get("cool_why") or "",
            "today": int(g.get("count") or 0) if g.get("day") == today else 0,
            "cap": int(config.load().get("xhs_daily_cap") or 400)}


def _before_hit():
    """每次去小红书读一条详情前调用：冷却中 / 今天超额就抛 Cooldown；否则随机歇几秒再放行。"""
    s = config.load()
    with _guard_lock:
        g = _guard()
        left = float(g.get("cool_until") or 0) - time.time()
        if left > 0:
            raise Cooldown(f"小红书防封冷却中，还剩 {int(left // 60) + 1} 分钟（{g.get('cool_why') or '被限流'}）")
        today = time.strftime("%Y-%m-%d")
        if g.get("day") != today:
            g["day"], g["count"] = today, 0
        cap = int(s.get("xhs_daily_cap") or 400)
        if g["count"] >= cap:
            raise Cooldown(f"今天已经读了 {g['count']} 条小红书详情（上限 {cap}），明天再继续，防封号")
        g["count"] += 1
        _guard_save(g)
        lo, hi = (s.get("xhs_delay") or [4, 9])[:2]
        wait = _last_hit[0] + random.uniform(float(lo), float(hi)) - time.time()
        _last_hit[0] = time.time() + max(0, wait)
    if wait > 0:
        time.sleep(wait)


# ---------------------------------------------------------------- 链接解析

def extract_links(text: str):
    """从粘贴内容里找出所有小红书链接（支持 App「复制链接」那种带口令的分享文字）。"""
    found = []
    for m in URL_RE.finditer(text or ""):
        u = m.group(0).rstrip(".,;:!?)）】」'\"，。")
        if any(h in u for h in HOSTS[:3]) and u not in found:
            found.append(u)
    for line in (text or "").splitlines():
        line = line.strip()
        if re.fullmatch(NOTE_ID, line) and line not in found:
            found.append(line)
    return found


def _match_id(url: str):
    for pat in ID_PATTERNS:
        m = pat.search(url or "")
        if m:
            return m.group(1), "note"
    return None, None


def remember(note_id, url):
    """记下笔记的完整链接（带 xsec_token），后面读详情时用。"""
    if note_id and url and "xsec_token" in url:
        _URLS[str(note_id)] = url


def note_url(note_id, url=None):
    if url and _match_id(url)[0] == note_id and ("xsec_token" in url or str(note_id) not in _URLS):
        return url
    return _URLS.get(str(note_id)) or f"https://www.xiaohongshu.com/explore/{note_id}"


def resolve(url: str):
    """把链接变成 (笔记ID, "note")。xhslink.com 短链跟一下跳转。图文和视频这时还分不出来，统一叫 note。"""
    if re.fullmatch(NOTE_ID, url or ""):
        return url, "note"
    nid, kind = _match_id(url)
    if nid:
        remember(nid, url)
        return nid, kind
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            final = r.geturl()
    except urllib.error.HTTPError as e:
        final = e.headers.get("Location") or ""
        if not final:
            raise DouyinError(f"链接打不开（HTTP {e.code}）")
    except urllib.error.URLError as e:
        raise DouyinError(f"网络连不上小红书：{e.reason}")
    nid, kind = _match_id(final)
    if nid:
        remember(nid, final)
        return nid, kind
    raise DouyinError("没能从链接里识别出笔记 ID，请确认是小红书笔记的分享链接")


# ---------------------------------------------------------------- XHS-Downloader 小进程

class _Bridge:
    """一个常驻的 XHS-Downloader 进程。线程安全（一次只问一条）。"""

    def __init__(self):
        self.p = None
        self.cookie = None
        self.lock = threading.Lock()

    def _python(self):
        for p in (XHS_DIR / ".venv" / "Scripts" / "python.exe", XHS_DIR / ".venv" / "bin" / "python"):
            if p.exists():
                return p
        raise DouyinError(f"没找到 XHS-Downloader：请先把它装到 {XHS_DIR}（git clone 后在目录里运行 uv sync）")

    def _start(self, cookie):
        self.close()
        tmp = DATA / "xhs_tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", XHS_COOKIE=cookie or "",
                   XHS_DELAY=os.environ.get("XHS_DELAY", "1"), XHS_QUALITY=os.environ.get("XHS_QUALITY", "low"))
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.p = subprocess.Popen([str(self._python()), "-X", "utf8", str(Path(__file__).with_name("xhs_bridge.py")),
                                   str(XHS_DIR), str(tmp)], cwd=str(XHS_DIR), env=env, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                  errors="replace", bufsize=1, creationflags=flags)
        self.cookie = cookie
        r = self._read(timeout=90)
        if not r.get("ready"):
            raise DouyinError("XHS-Downloader 没有正常启动")

    def _read(self, timeout=120):
        end = time.time() + timeout
        tail = []
        while time.time() < end:
            line = self.p.stdout.readline()
            if not line:
                self.close()
                raise DouyinError("XHS-Downloader 意外退出：" + " / ".join(tail[-3:])[:300])
            if line.startswith("@@RESULT "):
                return json.loads(line[9:])
            if line.strip():
                tail.append(line.strip())
        self.close()
        raise DouyinError("XHS-Downloader 太久没有响应")

    def extract(self, url, cookie=""):
        with self.lock:
            if self.p is None or self.p.poll() is not None or cookie != self.cookie:
                self._start(cookie)
            self.p.stdin.write(json.dumps({"url": url}) + "\n")
            self.p.stdin.flush()
            r = self._read()
        if not r.get("ok"):
            raise DouyinError(f"XHS-Downloader 出错：{r.get('error')}")
        return r.get("data") or {}

    def close(self):
        if self.p:
            try:
                self.p.stdin.close()
                self.p.wait(5)
            except Exception:
                try:
                    self.p.kill()
                except Exception:
                    pass
        self.p = None


BRIDGES = [_Bridge()]  # 只开 1 个小进程：一条一条读（同时读容易被封号）
BRIDGE = BRIDGES[0]
_free = __import__("queue").Queue()
for _b in BRIDGES:
    _free.put(_b)


_direct_off_until = 0.0  # 直连读详情被限流时，先停用一段时间，改走浏览器


def detail(note_id, url=None):
    """读一条笔记的详情（不需要浏览器，线程安全，一次一条）。"""
    global _direct_off_until
    if time.time() < _direct_off_until:
        raise DouyinError("直连读详情暂停中（被限流），改用备用方式")
    _before_hit()
    br = _free.get()
    try:
        target = note_url(note_id, url)
        # 默认不带登录 Cookie：带 xsec_token 的链接不登录就能读到（实测）。把浏览器里的登录 Cookie 拿给另一个程序用，
        # 等于同一个登录从两个不同的「设备」发请求——轻则这一步读不到、退回去开浏览器弹登录框，重则登录被顶掉。
        # 只有设置里明确打开 xhs_use_login_cookie 才带。
        use = "xsec_token" in target and config.load().get("xhs_use_login_cookie")
        d = br.extract(target, _saved_cookie() if use else "")
    except DouyinError as e:
        if "JSONDecodeError" in str(e):  # 小红书给了空页面：多半被限流，接下来 30 分钟不再直连
            _direct_off_until = time.time() + 1800
        raise
    finally:
        _free.put(br)
    if not d or not d.get("作品ID"):
        raise DouyinError("没读到笔记信息：可能笔记已删除 / 仅自己可见，或者链接缺少 xsec_token"
                          "（请从收藏列表同步，或在 App 里「分享 → 复制链接」）")
    return parse_detail(d, note_id)


def _saved_cookie():
    try:
        return COOKIE_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _clean_desc(s):
    s = re.sub(r"#([^#\[\]]+)\[话题\]#", r"#\1", s or "")
    s = re.sub(r"\[[^\[\]]{1,8}R\]", "", s)  # 小红书表情 [笑哭R]
    return s.strip()


def parse_detail(d: dict, note_id: str) -> dict:
    """XHS-Downloader 的结果 → 和 douyin.parse_aweme 一样的字段。"""
    urls = [u for u in d.get("下载地址") or [] if u]
    kind = d.get("作品类型") or ""
    is_video = kind == "视频"
    desc = _clean_desc(d.get("作品描述"))
    title = (d.get("作品标题") or "").strip() or (desc.splitlines()[0][:60] if desc else "") or f"小红书笔记 {note_id}"
    return {
        "aweme_id": note_id,
        "title": title,
        "desc": desc if not d.get("作品标题") else f"{d['作品标题']}\n\n{desc}".strip(),
        "author": d.get("作者昵称") or "",
        "duration": round(float(d.get("视频时长") or 0), 1),
        "cover": d.get("封面地址") or (urls[0] if urls and not is_video else None),
        "play_urls": urls if is_video else [],
        "hq_urls": urls if is_video else [],
        "subtitle_urls": [],
        "is_ad": False,
        "is_note": not is_video,
        "images": [] if is_video else [[u] for u in urls],
        "url": d.get("作品链接") or f"https://www.xiaohongshu.com/explore/{note_id}",
        "tags": d.get("作品标签") or "",
    }


def web_detail(note_id, url=None, log=print):
    """用网页解析站拿最低画质视频地址（不登录、不付费）。标题等信息由调用方补。"""
    from . import webparse
    r = webparse.parser(log).parse(webparse.strip_link(note_url(note_id, url)))
    if r.get("video"):
        d = {"作品ID": note_id, "作品类型": "视频", "下载地址": [r["video"]], "作品链接": note_url(note_id, url)}
    else:
        d = {"作品ID": note_id, "作品类型": "图文", "下载地址": r.get("images") or [], "作品链接": note_url(note_id, url)}
    out = parse_detail(d, note_id)
    out["title"] = "" if out["title"].startswith("小红书笔记 ") else out["title"]
    out["via"] = "web"
    return out


# ---------------------------------------------------------------- HTTP 下载

def _get(url, timeout=60):
    req = urllib.request.Request(url, headers={"Referer": REFERER, "User-Agent": UA})
    return urllib.request.urlopen(req, timeout=timeout)


def http_images(images, dest_dir: Path):
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for n, cands in enumerate(images, 1):
        for u in cands:
            try:
                with _get(u, 30) as r:
                    body = r.read()
                    ctype = (r.headers.get("content-type") or "").lower()
                if len(body) < 2048:
                    continue
                ext = ".webp" if "webp" in ctype else ".png" if "png" in ctype else ".heic" if "heic" in ctype else ".jpg"
                f = dest_dir / f"{n:02d}{ext}"
                f.write_bytes(body)
                saved.append(f)
                break
            except Exception:
                continue
        time.sleep(0.2)
    if not saved:
        raise DouyinError("图文的图片都下载失败了")
    return saved


def http_file(urls, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    last = "没有可用的视频地址"
    for u in urls:
        try:
            n = 0
            with _get(u, 120) as r, open(part, "wb") as f:
                while True:
                    b = r.read(512 * 1024)
                    if not b:
                        break
                    f.write(b)
                    n += len(b)
            if n < 10 * 1024:
                last = "下载到的文件太小，可能是错误页"
                continue
            part.replace(dest)
            return dest
        except Exception as e:
            last = str(e)[:150]
        finally:
            part.unlink(missing_ok=True)
    raise DouyinError(f"视频下载失败：{last}")


# ---------------------------------------------------------------- 浏览器（只在登录、读收藏列表时打开）

def _card(n: dict):
    """列表接口 / 网页数据里的一张笔记卡片 → 统一字段。认下划线和驼峰两种写法。"""
    c = n.get("note_card") or n.get("noteCard") or n
    nid = n.get("note_id") or n.get("noteId") or c.get("note_id") or c.get("noteId") or n.get("id")
    tok = n.get("xsec_token") or n.get("xsecToken") or c.get("xsec_token") or c.get("xsecToken") or ""
    if not nid or not re.fullmatch(NOTE_ID, str(nid)):
        return None
    user = c.get("user") or {}
    cover = c.get("cover") or {}
    url = f"https://www.xiaohongshu.com/explore/{nid}" + (f"?xsec_token={tok}&xsec_source=pc_collect" if tok else "")
    remember(nid, url)
    is_video = (c.get("type") == "video")
    title = (c.get("display_title") or c.get("displayTitle") or c.get("title") or "").strip()
    return {"aweme_id": str(nid), "title": title or f"小红书{'视频' if is_video else '笔记'} {nid}", "desc": "",
            "author": user.get("nickname") or user.get("nick_name") or user.get("nickName") or "",
            "duration": 0, "cover": cover.get("url_default") or cover.get("urlDefault") or cover.get("url") or None,
            "play_urls": [], "hq_urls": [], "subtitle_urls": [], "is_ad": False, "is_note": not is_video,
            "images": [], "url": url}


_DOM_CARDS_JS = r"""() => {
  const out = [];
  for (const a of document.querySelectorAll('a[href*="xsec_token"]')) {
    const m = a.href.match(/\/(?:explore|search_result|discovery\/item|board\/[0-9a-f]{24}|user\/profile\/[0-9a-f]{24})\/([0-9a-f]{24})\?(?:.*&)?xsec_token=([^&]+)/);
    if (!m) continue;
    const sec = a.closest('section') || a.parentElement;
    const t = sec && sec.querySelector('.title, .footer .title span');
    const au = sec && sec.querySelector('.author .name, .name');
    out.push({note_id: m[1], xsec_token: decodeURIComponent(m[2]),
              display_title: t ? t.textContent.trim() : '', user: {nickname: au ? au.textContent.trim() : ''},
              type: sec && sec.querySelector('.play-icon') ? 'video' : 'normal'});
  }
  return out;
}"""


# 网页里 Vue 状态（__INITIAL_STATE__）中已经加载的笔记：专辑页 = board.boardFeedsMap[专辑ID]，
# 个人主页 = user.notes[第几个标签]（0 笔记、1 收藏、2 赞过），是否还有更多看 user.noteQueries。
_STATE_NOTES_JS = r"""([where, key]) => {
  const s = window.__INITIAL_STATE__ || {};
  const v = x => x && x._value !== undefined ? x._value : (x && x.value !== undefined ? x.value : x);
  const plain = x => JSON.parse(JSON.stringify(v(x) || null));
  if (where === 'board') {
    const f = (plain(s.board && s.board.boardFeedsMap) || {})[key] || {};
    return {notes: f.notes || [], hasMore: f.hasMore !== false};
  }
  const notes = (plain(s.user && s.user.notes) || [])[key] || [];
  const q = (plain(s.user && s.user.noteQueries) || [])[key] || {};
  return {notes, hasMore: q.hasMore !== false};
}"""


_PAGE_NOTE_JS = r"""(id) => {
  const s = window.__INITIAL_STATE__ || {};
  const v = x => x && x._value !== undefined ? x._value : (x && x.value !== undefined ? x.value : x);
  const m = JSON.parse(JSON.stringify(v(s.note && s.note.noteDetailMap) || null)) || {};
  const e = m[id] || null;
  const n = e && (e.note || null);
  return n && (n.noteId || n.title || n.type) ? n : null;
}"""


def pick_site(has, pref=None):
    """先去哪个站：只有一个站有登录就去那个；两个都有 / 都没有，就去上次落在的那个（pref），再不行去 xiaohongshu.com。"""
    logged = [k for k in ("xiaohongshu.com", "rednote.com") if (has or {}).get(k)]
    if len(logged) == 1:
        return logged[0]
    return pref if pref in ("xiaohongshu.com", "rednote.com") else "xiaohongshu.com"


class Browser:
    """保存了登录态的真实浏览器窗口。只能在同一个线程里使用。读笔记详情不需要它。"""

    def __init__(self, preference="auto", log=print):
        self.preference = preference
        self.log = log
        self.pw = self.ctx = self.page = None
        self.origin = "https://www.xiaohongshu.com"

    def start(self):  # 懒启动：只有登录 / 读列表才真的打开浏览器
        return

    def _channels(self):
        p = self.preference
        return [p] if p in ("msedge", "chrome", "chromium") else ["msedge", "chrome", "chromium"]

    def _open(self):
        if self.ctx:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise DouyinError("缺少 playwright 组件，请重新运行安装")
        self.pw = pw_acquire()
        errors = []
        for ch in self._channels():
            try:
                kw = dict(user_data_dir=str(BROWSER_DIR / ch), headless=False, viewport={"width": 1280, "height": 860},
                          locale="zh-CN", args=["--disable-blink-features=AutomationControlled"],
                          ignore_default_args=["--enable-automation"])
                if ch != "chromium":
                    kw["channel"] = ch
                self.ctx = self.pw.chromium.launch_persistent_context(**kw)
                self.ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
                self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
                self.log(f"已打开浏览器（{ {'msedge': 'Edge', 'chrome': 'Chrome', 'chromium': '内置 Chromium'}[ch] }）")
                return
            except Exception as e:
                errors.append(f"{ch}: {str(e).splitlines()[0][:120]}")
        self.close()
        raise DouyinError("找不到可用的浏览器。请安装 Microsoft Edge 或 Google Chrome。\n" + "\n".join(errors))

    def close(self):
        for fn in (lambda: self.ctx and self.ctx.close(), lambda: self.pw and pw_release(self.pw)):
            try:
                fn()
            except Exception:
                pass
        self.pw = self.ctx = self.page = None

    # ---- 登录
    def _state_user(self):
        try:
            return self.page.evaluate("""() => {
              const s = window.__INITIAL_STATE__ && window.__INITIAL_STATE__.user;
              if (!s) return null;
              const v = x => x && x._value !== undefined ? x._value : (x && x.value !== undefined ? x.value : x);
              const u = v(s.userInfo) || {};
              return {loggedIn: !!v(s.loggedIn), userId: u.userId || u.user_id || '', guest: !!u.guest,
                      nickname: u.nickname || ''};
            }""") or {}
        except Exception:
            return {}

    def logged_in(self) -> bool:
        u = self._state_user()
        return bool(u.get("loggedIn") and u.get("userId") and not u.get("guest"))

    def _goto_home(self):
        """去小红书首页，并认定「这台电脑现在用哪个站」（self.origin），之后所有地址都用它。

        小红书有两个站：xiaohongshu.com 和 rednote.com（海外网络打开前者会被跳到后者），两边的登录不通用。
        以前每次都先去 xiaohongshu.com：在海外网络上会被跳到 rednote.com，那边如果没登录就又要扫码，
        扫一次就多一个新的网页登录。现在先去「上次落在的那个站 / 有登录的那个站」，能不换站就不换站。"""
        if not self.page.url.startswith(("https://www.xiaohongshu.com", "https://www.rednote.com")):
            site = pick_site(self.site_logins(), config.load().get("xhs_site"))
            self.page.goto(self.SITES[site], wait_until="domcontentloaded", timeout=60000)
            self.page.wait_for_timeout(2500)
        self._note_origin()

    def _note_origin(self):
        m = re.match(r"https://[^/]+", self.page.url)
        if m:
            self.origin = m.group(0)
            site = self._site()
            if config.load().get("xhs_site") != site:  # 记住这个网络落在哪个站，下次直接去
                config.save({"xhs_site": site})

    def _try_other_site(self):
        """当前站没登录、另一个站有登录：去另一个站看一次（换了网络可能就不会再被跳走）。返回现在所在的站。"""
        site, has = self._site(), self.site_logins()
        other = next((k for k in self.SITES if k != site), None)
        if other and has.get(other):
            try:
                self.page.goto(self.SITES[other], wait_until="domcontentloaded", timeout=60000)
                self.page.wait_for_timeout(2500)
            except Exception:
                pass
            if self._site() != site:
                self.log(f"小红书：{site} 没登录，{other} 有登录，已切到 {other}。")
                self._sess0 = None
            else:
                self.log(f"小红书：登录是在 {other} 上的，但这个网络打开它会被跳回 {site}，所以要在 {site} 上再登录一次"
                         f"（只需要这一次；之后都用 {site}）。")
        self._note_origin()
        return self._site()

    def _save_cookie(self):
        """把登录 Cookie 存一份给 XHS-Downloader 用——只有设置里打开 xhs_use_login_cookie 才存（默认不存、不用）。"""
        if not config.load().get("xhs_use_login_cookie"):
            return
        try:
            cs = [c for c in self.ctx.cookies() if "xiaohongshu.com" in c.get("domain", "")]
            if cs:
                COOKIE_PATH.write_text("; ".join(f"{c['name']}={c['value']}" for c in cs), encoding="utf-8")
        except Exception:
            pass

    def _dismiss_login(self):
        """只是读公开笔记时不需要登录：页面自己弹出的登录框关掉，免得让人以为又要扫码（多扫一次就多一个网页登录）。"""
        try:
            hit = self.page.evaluate("""() => {
              const box = document.querySelector('.login-container, .login-modal, [class*="login-container"], [class*="login-modal"]');
              if (!box) return '';
              const x = box.querySelector('.close-button, .close-icon, [class*="close"]');
              if (x) { x.click(); return 'closed'; }
              return 'found';
            }""")
            if hit == "found":
                self.page.keyboard.press("Escape")
            return bool(hit)
        except Exception:
            return False

    SITES = {"xiaohongshu.com": "https://www.xiaohongshu.com/explore", "rednote.com": "https://www.rednote.com/explore"}

    def _site(self, url=None):
        """当前窗口在哪个站：xiaohongshu.com（国内）或 rednote.com（海外网络会被跳过去）。两个站的登录不通用。"""
        u = url or (self.page.url if self.page else "")
        return "rednote.com" if "rednote.com" in u else "xiaohongshu.com"

    def _session(self, site=None):
        """当前站的 web_session（游客也有，只用来判断扫码后有没有变）。"""
        site = site or self._site()
        try:
            return next((c["value"] for c in self.ctx.cookies()
                         if c["name"] == "web_session" and c.get("domain", "").endswith(site)), "")
        except Exception:
            return ""

    def site_logins(self):
        """两个站各自有没有登录凭证（id_token 只有真登录后才有，游客没有）。"""
        has = {k: False for k in self.SITES}
        try:
            for c in self.ctx.cookies():
                d = c.get("domain", "")
                for k in has:
                    if d.endswith(k) and c["name"] == "id_token" and c.get("value"):
                        has[k] = True
        except Exception:
            pass
        return has

    def _diagnose(self):
        """没登录时留个底：页面截图 + 有哪些登录相关 Cookie（只记名字和长度，不记值），方便查扫码后为什么还是没登录。"""
        try:
            self.page.screenshot(path=str(DATA / "xhs_login_check.png"))
            cs = {}
            for c in self.ctx.cookies():
                d = c.get("domain", "")
                if "xiaohongshu" in d or "rednote" in d:
                    cs.setdefault(d, {})[c["name"]] = len(c.get("value") or "")
            info = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "url": self.page.url, "state_user": self._state_user(),
                    "cookies": cs, "sess0_len": len(getattr(self, "_sess0", None) or "")}
            (DATA / "xhs_login_check.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as e:
            self.log(f"（登录诊断没存下：{e}）")

    def check_login(self, prompt=True) -> bool:
        """只检查一次登录状态，不等待。没登录就把窗口提到最前面、点开扫码登录（只点第一次，免得二维码一直换）。
        用户说「登好了」再调一次：扫码后 web_session 变了但网页没刷新，就只刷新这一次再看。
        小红书有两个站（xiaohongshu.com / 海外网络跳到的 rednote.com），登录不通用：当前站没登录、
        另一个站有登录，就去另一个站看一次（网络换了可能就不会再被跳走）。结果说明放在 self.last_check。"""
        self._open()
        self._goto_home()
        site = self._site()
        if not self.logged_in() and getattr(self, "_sess0", None) is not None and self._session(site) != self._sess0:
            try:
                self.page.reload(wait_until="domcontentloaded", timeout=30000)
                self.page.wait_for_timeout(2500)
            except Exception:
                pass
        has = self.site_logins()
        if not self.logged_in():
            self._try_other_site()
        site = self._site()
        self._note_origin()
        ok = self.logged_in()
        self.last_check = {"site": site, "has": has, "ok": ok}
        if ok:
            self._save_cookie()
            self._sess0 = None
            return True
        self._diagnose()
        if prompt:
            try:
                self.page.bring_to_front()
            except Exception:
                pass
            if getattr(self, "_sess0", None) is None:
                self._sess0 = self._session(site)
                try:
                    self.page.get_by_text("登录", exact=True).first.click(timeout=5000)
                except Exception:
                    pass
        return False

    def ensure_login(self, timeout=1800, should_stop=lambda: False):
        self._open()
        self._goto_home()
        if not self.logged_in():
            self._try_other_site()  # 另一个站有登录就先去那边看看，别急着让人再扫一次码
        if self.logged_in():
            self._save_cookie()
            self.log(f"小红书（{self._site()}）已经是登录状态。浏览器窗口先留着。")
            return True
        site = self._site()
        self.log(f"请在弹出的浏览器窗口里用小红书 App 扫码登录 {site}（左侧「登录」）。只需要扫这一次，最多等 30 分钟，不着急……")
        try:
            self.page.bring_to_front()
            self.page.get_by_text("登录", exact=True).first.click(timeout=5000)
        except Exception:
            pass

        def session():  # 只看当前这个站的 web_session（两个站各有一份，混着看会判断错）
            return self._session(site)

        first = session()
        end = time.time() + timeout
        while time.time() < end:  # 只看本地状态，不刷新页面：反复刷新会换二维码、触发请求上限
            if should_stop():
                return False
            if self.logged_in():
                self.log("登录成功，登录状态已保存，下次不用再扫码。")
                self.page.wait_for_timeout(1500)
                self._save_cookie()
                return True
            self.page.wait_for_timeout(3000)
            cur = session()
            if cur and cur != first:  # 扫码成功会换 web_session；网页没自己刷新时才刷新这一次
                first = cur
                self.page.wait_for_timeout(3000)
                if not self.logged_in():
                    try:
                        self.page.reload(wait_until="domcontentloaded", timeout=30000)
                    except Exception:
                        pass
        raise NotLoggedIn("等了 30 分钟还没登录，已取消。")

    def _self_id(self):
        u = self._state_user()
        if not u.get("userId"):
            raise NotLoggedIn("没读到自己的账号，请先登录小红书")
        return u["userId"]

    # ---- 单条笔记（XHS-Downloader）
    def fetch_detail(self, note_id: str, kind: str = "note", url: str = None) -> dict:
        """读详情的顺序（越往后对账号风险越大）：
        1. XHS-Downloader（不开浏览器，最低画质、带音轨）
        2. 网页解析站（kukutool，免登录、不付费，跟你的账号无关）
        3. 登录好的浏览器窗口里打开笔记页（最后才用，而且冷却中不用）"""
        errs = []
        try:
            return detail(note_id, url)
        except Cooldown as e:
            errs.append(str(e))
        except DouyinError as e:
            errs.append(str(e))
            if "限流" not in str(e) and "JSONDecodeError" not in str(e) and "没读到笔记信息" not in str(e):
                raise
        if config.load().get("web_parse_fallback", True):
            try:
                return web_detail(note_id, url, log=self.log)
            except Exception as e:
                errs.append(str(e))
        if guard_status()["cool_left"]:
            raise Cooldown("；".join(errs))
        return self.page_detail(note_id, url)

    def page_detail(self, note_id: str, url: str = None) -> dict:
        """在登录好的浏览器窗口里打开笔记页，从网页数据里读详情（和人手动打开一样）。"""
        self._open()
        _before_hit()
        if not getattr(self, "_origin_known", False):  # 先弄清这个网络用哪个站，笔记页直接开在那个站上，不来回跳
            self._goto_home()
            self._origin_known = True
            self.log("小红书窗口里如果弹出登录框，不用管它：读公开笔记、下载视频都不需要登录（程序会自己关掉）。")
        target = re.sub(r"^https://www\.(?:xiaohongshu|rednote)\.com", self.origin, note_url(note_id, url))
        self.page.goto(target, wait_until="domcontentloaded", timeout=60000)
        n = None
        for _ in range(10):
            self.page.wait_for_timeout(1000)
            self._dismiss_login()
            n = self.page.evaluate(_PAGE_NOTE_JS, note_id)
            if n:
                break
        if not n:
            if "captcha" in self.page.url or "verify" in self.page.url:
                cooldown(60, "要求验证")
                raise Cooldown("小红书要求验证：先停 1 小时；可以在浏览器窗口里手动完成验证")
            raise DouyinError("网页里没读到笔记信息（可能已删除 / 仅自己可见）")
        is_video = n.get("type") == "video"
        urls = []
        if is_video:
            st = ((n.get("video") or {}).get("media") or {}).get("stream") or {}
            # 所有编码（h264 / h265 / 新的 EF4~EF7 …）里只要自带音轨的完整 mp4，从小到大（最低画质优先）；
            # 登录后常给只有画面的 DASH 分片（.m4s，音频另一路），下下来没声音
            streams = [it for v in st.values() if isinstance(v, list) for it in v if isinstance(it, dict)]

            def ok(it):
                u = it.get("masterUrl") or next(iter(it.get("backupUrls") or []), "") or ""
                return ((it.get("audioChannels") or 0) > 0 or it.get("audioCodec")) and ".m4s" not in u \
                    and (it.get("format") or "").lower() not in ("m4s", "dash")
            good = sorted((it for it in streams if ok(it)), key=lambda it: (it.get("size") or 0, it.get("height") or 0))
            for it in good or streams:
                for u in [it.get("masterUrl")] + list(it.get("backupUrls") or []):
                    if u and u not in urls:
                        urls.append(u)
        else:
            for im in n.get("imageList") or []:
                u = im.get("urlDefault") or im.get("url") or next(
                    (x.get("url") for x in im.get("infoList") or [] if x.get("url")), None)
                if u:
                    urls.append(u)
        cap = (n.get("video") or {}).get("capa") or {}
        tags = " ".join("#" + t.get("name", "") for t in n.get("tagList") or [] if t.get("name"))
        d = {"作品ID": note_id, "作品类型": "视频" if is_video else "图文", "作品标题": n.get("title") or "",
             "作品描述": n.get("desc") or "", "作者昵称": (n.get("user") or {}).get("nickname") or "",
             "视频时长": float(cap.get("duration") or 0), "下载地址": urls,
             "作品链接": note_url(note_id, url), "作品标签": tags}
        if not urls:
            raise DouyinError("网页里没读到下载地址")
        return parse_detail(d, note_id)

    def fetch_text(self, url) -> str:
        with _get(url, 30) as r:
            return r.read().decode("utf-8", "ignore")

    def download_images(self, images, dest_dir: Path):
        return http_images(images, dest_dir)

    def download(self, urls, dest: Path) -> Path:
        return http_file(urls, dest)

    # ---- 收藏 / 点赞列表
    def _collect(self, url, match, on_item, limit, should_stop, click=(), dom=False, state=None, wait_user=0, label=""):
        """打开页面，旁听列表接口 + 读网页里已有的卡片，一边往下滚一边收集。返回条数。
        wait_user：页面上一直没有结果（要验证 / 登录 / 加载慢）时，最多等你这么多秒，每 5 秒看一次。"""
        hits, seen = [], set()

        def on_resp(r):
            if match(r.url):
                hits.append(r)

        def take(d):
            if d and d["aweme_id"] not in seen and len(seen) < limit:
                seen.add(d["aweme_id"])
                on_item(d)
                return 1
            return 0

        self.page.on("response", on_resp)
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
            self.page.wait_for_timeout(3000)
            for text in click:
                if hits:
                    break
                try:
                    self.page.get_by_text(text, exact=True).first.click(timeout=4000)
                    self.page.wait_for_timeout(2000)
                except Exception:
                    pass
            waited = 0
            while wait_user and waited < wait_user and not hits and not should_stop():
                try:
                    if dom and self.page.evaluate(_DOM_CARDS_JS):
                        break
                except Exception:
                    pass
                if not waited:
                    self.log(f"{label or '小红书'}页面还没出结果。如果弹出的浏览器窗口里要验证或登录，请完成一下，"
                             "完成后会自动继续（每 5 秒看一次，最多等 10 分钟）。")
                    try:
                        self.page.bring_to_front()
                    except Exception:
                        pass
                self.page.wait_for_timeout(5000)
                waited += 5
            idle, has_more = 0, True
            while len(seen) < limit and idle < 6 and not should_stop():
                new = 0
                while hits:
                    r = hits.pop(0)
                    try:
                        j = r.json()
                    except Exception:
                        continue
                    data = j.get("data") or {}
                    if data.get("has_more") is False:
                        has_more = False
                    if not getattr(self, "_raw_saved", False):  # 留一份原始数据，看接口里有没有收藏时间
                        try:
                            from .config import DATA as _D
                            (_D / "_raw_xhs_collect.json").write_text(json.dumps({"url": r.url, "keys": list(j), "data_keys": list((j.get("data") or {}) if isinstance(j.get("data"), dict) else {}), "sample": {"cursor": data.get("cursor"), "has_more": data.get("has_more"), "first": (data.get("notes") or [])[:2]}}, ensure_ascii=False, indent=1)[:200000], encoding="utf-8")
                            self._raw_saved = True
                        except Exception:
                            pass
                    for n in (data.get("notes") or data.get("items") or []):  # 搜索结果放在 items 里
                        if isinstance(n, dict) and n.get("model_type") not in (None, "note"):
                            continue  # 搜索结果里夹的「大家都在搜」等
                        new += take(_card(n))
                if state:  # 已经加载进网页的笔记（第一页通常直接写在网页里，不走接口）
                    try:
                        st = self.page.evaluate(_STATE_NOTES_JS, list(state)) or {}
                        for n in st.get("notes") or []:
                            new += take(_card(n))
                        if st.get("hasMore") is False:
                            has_more = False
                    except Exception:
                        pass
                if dom:  # 专辑页：第一页有时直接写在网页里，不走接口（个人主页不能这样读，会混进自己发的笔记）
                    try:
                        for n in self.page.evaluate(_DOM_CARDS_JS) or []:
                            new += take(_card(n))
                    except Exception:
                        pass
                if not has_more and not hits:
                    break
                idle = 0 if new else idle + 1
                try:
                    self.page.mouse.move(640, 560)
                    self.page.mouse.wheel(0, random.randint(1800, 2800))
                except Exception:
                    pass
                self.page.wait_for_timeout(random.randint(1200, 2200))  # 慢一点，别被风控
            return len(seen)
        finally:
            self.page.remove_listener("response", on_resp)

    def sync_list(self, kind="collection", limit=50, on_item=lambda d: None, should_stop=lambda: False):
        """kind = collection（收藏）或 like（点赞）。返回读到的条数。"""
        self.ensure_login(should_stop=should_stop)
        uid = self._self_id()
        if kind == "like":
            url, label = f"{self.origin}/user/profile/{uid}?tab=liked", "赞过"
            match = lambda u: "/api/sns/web/v1/note/like/page" in u
        else:
            url, label = f"{self.origin}/user/profile/{uid}?tab=fav&subTab=note", "收藏"
            match = lambda u: "/api/sns/web/v2/note/collect/page" in u
        n = self._collect(url, match, on_item, limit, should_stop, click=(label,),
                          state=("user", 2 if kind == "like" else 1))
        if not n:
            raise DouyinError(f"没读到「{label}」列表。请看一眼浏览器窗口：是否需要验证，或者列表是否为空、设成了不公开。")
        return n

    def search(self, keyword, limit=30, on_item=lambda d: None, should_stop=lambda: False):
        """用小红书网页版的搜索搜关键词（和人手动搜一样，一页页慢慢往下滚）。返回读到的条数。"""
        import urllib.parse
        self.ensure_login(should_stop=should_stop)
        url = f"{self.origin}/search_result?keyword={urllib.parse.quote(keyword)}&source=web_explore_feed"
        match = lambda u: "/search/notes" in u and "/api/sns/web/" in u  # v1 / v2 都认（现在是 so.xiaohongshu.com 的 v2）

        got = [0]

        def item(d):  # 搜索来的链接要带 pc_search，后面读详情才认
            if d.get("url"):
                d["url"] = d["url"].replace("xsec_source=pc_collect", "xsec_source=pc_search")
                remember(d["aweme_id"], d["url"])
            got[0] += 1
            on_item(d)

        urls = []

        def watch(r):  # 诊断用：记下带 search 的请求
            if "search" in r.url and len(urls) < 80:
                urls.append(r.url[:220])

        self.page.on("response", watch)
        try:
            return self._collect(url, match, item, limit, should_stop, dom=True, wait_user=600, label="代刷：小红书搜索")
        finally:
            self.page.remove_listener("response", watch)
            from .douyin import _search_debug
            _search_debug("xhs", self.page, url, urls, got[0])

    def list_folders(self, should_stop=lambda: False):
        """返回收藏专辑列表 [{"id", "name", "count"}]。"""
        self.ensure_login(should_stop=should_stop)
        uid = self._self_id()
        hits = []

        def on_resp(r):
            if "/api/sns/web/v1/board/user" in r.url:
                hits.append(r)

        self.page.on("response", on_resp)
        out = {}
        try:
            self.page.goto(f"{self.origin}/user/profile/{uid}?tab=fav&subTab=board",
                           wait_until="domcontentloaded", timeout=60000)
            self.page.wait_for_timeout(3000)
            if not hits:
                for text in ("收藏", "专辑"):
                    try:
                        self.page.get_by_text(text, exact=True).first.click(timeout=4000)
                        self.page.wait_for_timeout(2000)
                    except Exception:
                        pass
            for _ in range(8):  # 专辑多的话往下滚几次
                while hits:
                    try:
                        j = hits.pop(0).json()
                    except Exception:
                        continue
                    for b in (j.get("data") or {}).get("boards") or []:
                        bid = str(b.get("id") or b.get("board_id") or "")
                        if bid:
                            out[bid] = {"id": bid, "name": b.get("name") or "未命名专辑",
                                        "count": int(b.get("total") or b.get("note_count") or b.get("total_count") or 0)}
                if should_stop():
                    break
                self.page.mouse.move(640, 560)
                self.page.mouse.wheel(0, 2000)
                self.page.wait_for_timeout(1500)
                if not hits:
                    break
            try:  # 第一页专辑直接写在网页里（board.userBoardList）
                for b in self.page.evaluate("""() => { const s = window.__INITIAL_STATE__ || {};
                    const v = x => x && x._value !== undefined ? x._value : (x && x.value !== undefined ? x.value : x);
                    return JSON.parse(JSON.stringify(v(s.board && s.board.userBoardList) || [])); }""") or []:
                    bid = str(b.get("id") or b.get("boardId") or "")
                    if bid:
                        out[bid] = {"id": bid, "name": b.get("name") or "未命名专辑", "count": int(b.get("total") or 0)}
            except Exception:
                pass
            if not out:  # 兜底：从网页上的专辑链接读
                for b in self.page.evaluate(r"""() => [...document.querySelectorAll('a[href*="/board/"]')].map(a => {
                    const m = a.href.match(/\/board\/([0-9a-f]{24})/);
                    return m ? {id: m[1], name: (a.querySelector('.title, .name') || a).textContent.trim()} : null;
                  }).filter(Boolean)""") or []:
                    out.setdefault(b["id"], {"id": b["id"], "name": b["name"][:40] or "未命名专辑", "count": 0})
        finally:
            self.page.remove_listener("response", on_resp)
        return list(out.values())

    def sync_folder(self, folder_id, limit=50, on_item=lambda d: None, should_stop=lambda: False):
        """读取某个收藏专辑里的笔记，每条调用 on_item。返回条数。"""
        self.ensure_login(should_stop=should_stop)
        n = self._collect(f"{self.origin}/board/{folder_id}", lambda u: "/api/sns/web/v1/board/note" in u,
                          on_item, limit, should_stop, dom=True,
                          state=("board", str(folder_id)))
        if not n:
            raise DouyinError("没读到这个专辑里的笔记。请看一眼浏览器窗口：是否需要验证，或者专辑是否为空。")
        return n
