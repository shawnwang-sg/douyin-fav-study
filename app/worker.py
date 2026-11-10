"""后台处理线程：一条一条地 读信息 → 下载 → 转文字 → AI 提炼 → 写笔记。

浏览器只能在同一个线程里操作，所以所有抖音相关的事都放在这个线程里排队做。
"""
import collections
import concurrent.futures
import json
import urllib.request
import queue
import random
import shutil
import threading
import time
import traceback
from pathlib import Path

from . import config, db, douyin, llm, notes, ocr, progress, transcribe, xhs, yuedu

MEDIA_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".flv", ".avi", ".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}
FORCE_MARK = "你选了加入整理"  # 跳过的条目，用户点「加入整理」后重新排队：不再按长度 / 没信息量跳过
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic"}


class Worker:
    def __init__(self):
        self.cmds = queue.Queue()
        self.logs = collections.deque(maxlen=300)
        self.state = {"busy": False, "task": "", "item_id": None, "stage": "", "done": 0, "total": 0,
                      "last_error": None}  # 最近一次登录 / 同步失败的原因，网页上会显示成提示条
        self._stop = threading.Event()
        self.browsers = {}  # 平台 → 浏览器（抖音、小红书各一个，登录态分开）
        self.retry_later = []
        self.force_extract = _load_force()  # 每周 / 按月整理要润色进学习库的条目；存盘，重启后照样润色
        self.priority = _load_force(PRIORITY_PATH)  # 代刷来的：插队，排在队列里所有别的条目前面先处理
        self.urgent = queue.Queue()  # 插队的登录检查：正在处理队列时，在两条之间做，不用等整批处理完
        self.login = {}  # 平台 → {"ok": True/False/None(检查中), "t": 时间, "msg": 说明}
        self.prog = progress.Progress()
        self.downloads = []  # 「仅下载」任务：不进学习库，不转文字，不用 AI
        self._dl_seq = 0
        self.thread = threading.Thread(target=self._run, daemon=True, name="worker")
        self.thread.start()
        if self.force_extract:  # 上次每周 / 按月整理没做完（中途关了程序）：启动后接着做
            self.log(f"上次整理还有 {len(self.force_extract)} 条没做完，接着处理。")
            self.submit("process")

    # ------------------------------------------------------------ 对外接口
    def log(self, msg):
        self.logs.append({"t": time.time(), "msg": str(msg)})
        print(time.strftime("%H:%M:%S"), msg, flush=True)

    def submit(self, cmd, **kw):
        self._stop.clear()
        self.cmds.put((cmd, kw))

    def _queue_download(self, url, aweme_id="", title="", author="", kind="", subdir="", media=None, platform=None):
        """加一条「仅下载」任务；同一个作品（同一个目标文件夹）还在排队/下载中就不重复加。返回是否加了。

        subdir：放进下载目录下的哪个子文件夹（同步收藏夹分类时 = 分类名）。
        media：列表接口里已经带的视频/图片地址，有就直接下，省掉逐条打开页面。
        """
        for d in self.downloads:
            if d["status"] in ("queued", "running") and d.get("subdir", "") == subdir and (
                    d["url"] == url or (aweme_id and d.get("aweme_id") == aweme_id)):
                return False
        self._dl_seq += 1
        platform = platform or (config.platform_of(aweme_id) if aweme_id else _platform_of_url(url))
        self.downloads.append({"id": self._dl_seq, "url": url, "aweme_id": aweme_id, "status": "queued",
                               "platform": platform,
                               "title": title, "author": author, "kind": kind, "path": "", "error": "",
                               "subdir": subdir, "media": media, "t": time.time()})
        # 只保留最近 5000 条已结束的记录；排队中的不能丢
        ended = [d for d in self.downloads if d["status"] not in ("queued", "running")]
        if len(ended) > 5000:
            drop = {id(d) for d in ended[:-5000]}
            self.downloads[:] = [d for d in self.downloads if id(d) not in drop]
        return True

    def add_downloads(self, links):
        """把链接加入「仅下载」队列，返回新加的条数。"""
        n = sum(self._queue_download(link) for link in links)
        if n:
            self.submit("download")
        return n

    def download_counts(self):
        c = collections.Counter(d["status"] for d in self.downloads)
        return {k: c.get(k, 0) for k in ("queued", "running", "done", "skipped", "failed")}

    def request_login_check(self, platforms, reopen=False):
        if self.state.get("busy"):
            self.urgent.put(("login", dict(platforms=list(platforms), reopen=reopen)))
        else:
            self.submit("login_check", platforms=list(platforms), reopen=reopen)

    def request_topic_feed(self, **kw):
        """代刷：正在处理队列时插队——在两条之间先去搜，搜到的新视频直接进正在处理的队列，不用等整批做完。"""
        if self.state.get("busy"):
            self.urgent.put(("feed", kw))
        else:
            self.submit("topic_feed", **kw)

    def _service_urgent(self):
        while True:
            try:
                kind, kw = self.urgent.get_nowait()
            except queue.Empty:
                return
            stage = self.state.get("stage")
            try:
                if kind == "feed":
                    self._topic_feed(**kw, process=False)
                else:
                    self._login_check(kw["platforms"], kw["reopen"])
            except Exception as e:
                self.log(f"⚠ {'代刷' if kind == 'feed' else '登录检查'}出错：{e}")
            finally:
                self.state["stage"] = stage

    def stop(self):
        self._stop.set()
        self.log("收到停止指令，处理完当前这一步就停。")

    def stopping(self):
        return self._stop.is_set()

    # ------------------------------------------------------------ 主循环
    def _run(self):
        while True:
            try:
                cmd, kw = self.cmds.get(timeout=1)
            except queue.Empty:
                if not self.urgent.empty():  # 刚好在忙完的一瞬间插队的登录检查
                    self.state.update(busy=True, task="login_check")
                    self._service_urgent()
                    self.state.update(busy=False, task="", stage="")
                continue
            self.state.update(busy=True, task=cmd, stage="", item_id=None)
            if cmd in ("login", "sync"):
                self.state["last_error"] = None
            try:
                if cmd == "process":
                    self._process_all(kw.get("ids"))
                elif cmd == "login":
                    self._browser(kw.get("platform", "douyin")).ensure_login(should_stop=self.stopping)
                elif cmd == "sync":
                    self._sync(kw.get("kind", "collection"), kw.get("limit", 50), kw.get("then_process", True),
                               folder=kw.get("folder"), folder_name=kw.get("folder_name", ""),
                               download_only=kw.get("download_only", False), platform=kw.get("platform", "douyin"))
                elif cmd == "login_check":
                    self._login_check(kw.get("platforms") or ["douyin", "xhs"], kw.get("reopen", False))
                elif cmd == "weekly_sync":
                    self._weekly_sync(kw.get("platforms") or ["douyin", "xhs"], kw.get("days", 7), kw.get("full", False))
                elif cmd == "topic_feed":
                    self._topic_feed(kw["topic_id"], kw["keyword"], kw.get("n", 5), kw.get("platforms") or ["douyin", "xhs"])
                elif cmd == "folders":
                    self._load_folders(kw.get("platform", "douyin"))
                elif cmd == "download":
                    self._download_all()
            except douyin.DouyinError as e:
                self.log(f"⚠ {e}")
                self._set_error(cmd, str(e))
            except Exception as e:
                self.log(f"⚠ 出错了：{e}")
                self._set_error(cmd, f"出错了：{e}")
                traceback.print_exc()
            finally:
                self._service_urgent()
                if self.cmds.empty():
                    if not self.login_pending():  # 有平台正等着扫码登录：窗口都先留着
                        self._close_browser(keep="xhs")  # 小红书窗口留着：反复开关、重新登录最容易被风控
                    self.state.update(busy=False, task="", stage="", item_id=None)

    def _set_error(self, cmd, msg):
        if cmd in ("login", "sync"):
            self.state["last_error"] = {"task": cmd, "msg": msg, "t": time.time()}

    def clear_error(self):
        self.state["last_error"] = None

    def _browser(self):
        if self.browser is None:
            self.browser = douyin.Browser(config.load().get("browser", "auto"), log=self.log)
        self.browser.start()
        return self.browser
    @staticmethod
    def _mod(platform):
        return xhs if platform == "xhs" else douyin

    def _browser(self, platform="douyin"):
        b = self.browsers.get(platform)
        if b is None:
            b = self.browsers[platform] = self._mod(platform).Browser(config.load().get("browser", "auto"), log=self.log)
        b.start()
        return b

    def _close_browser(self, keep=None, only=None):
        for k in list(self.browsers):
            if k == keep or (only and k != only):
                continue
            try:
                self.browsers.pop(k).close()
            except Exception:
                pass

    def _detail(self, platform, aweme_id, kind, url=None):
        """读一条作品的详情（标题、作者、文件地址）；没登录就先登录再读一次。"""
        b = self._browser(platform)

        def call():
            return b.fetch_detail(aweme_id, kind, url) if platform == "xhs" else b.fetch_detail(aweme_id, kind)
        try:
            return call()
        except douyin.NotLoggedIn:
            b.ensure_login(should_stop=self.stopping)
            return call()

    # ------------------------------------------------------------ 收藏夹同步
    def _load_folders(self, platform="douyin"):
        word = "收藏专辑" if platform == "xhs" else "收藏夹分类"
        self.log(f"读取{config.PLATFORMS[platform]}{word}……")
        folders = self._browser(platform).list_folders(should_stop=self.stopping)
        key = "xhs_folders" if platform == "xhs" else "folders"
        config.save({key: folders, key + "_at": time.time()})
        self.log(f"读到 {len(folders)} 个{word}：" + "、".join(f["name"] for f in folders))

    def _save_favorites(self, platform, rows, subdir=""):
        """把这次读到的收藏列表（标题 + 链接）存成中间文件，以后补下载不用再刷一遍列表（防风控）。"""
        f = config.DATA / f"favorites_{platform}{'_' + subdir if subdir else ''}.json"
        old = {}
        if f.exists():
            try:
                old = {r["aweme_id"]: r for r in json.loads(f.read_text(encoding="utf-8"))}
            except Exception:
                pass
        for r in rows:
            r["downloaded"] = downloaded_path(r["aweme_id"]) or ""
            old[r["aweme_id"]] = {**old.get(r["aweme_id"], {}), **r}
        f.write_text(json.dumps(list(old.values()), ensure_ascii=False, indent=1), encoding="utf-8")

    def _sync(self, kind, limit, then_process, folder=None, folder_name="", download_only=False,
              platform="douyin", known_stop=0, since=None):
        """读收藏列表。known_stop > 0：连续碰到这么多条已经有的就停（增量同步：新收藏都在最前面）。
        since（时间戳）：把这个时间之后收藏的都过一遍才停。抖音按接口给的收藏时间；小红书接口不给收藏时间，
        按本工具第一次看到它的时间，连续 20 条都早于 since 才停。"""
        pname = config.PLATFORMS[platform]
        if platform == "xhs":
            name = f"专辑 · {folder_name or folder}" if folder else ("赞过" if kind == "like" else "收藏")
        else:
            name = f"收藏夹 · {folder_name or folder}" if folder else ("喜欢" if kind == "like" else "收藏")
        self.log(f"开始读取{pname}「{name}」列表（最多 {limit} 条）{'，只下载' if download_only else ''}……")
        added, requeued, notes_n = [0], [0], [0]
        rows, known = [], [0]
        new_ids = []
        past = [0]  # since 模式：已经读到早于 since 的（抖音：1 就停；小红书：连续 20 条）

        def stop_now():
            if self.stopping() or (known_stop and known[0] >= known_stop):
                return True
            return bool(since) and past[0] >= (1 if platform == "douyin" else 20)

        def seen(d):
            rows.append({k: d.get(k) for k in ("aweme_id", "title", "author", "url", "is_note", "duration")})
            if platform == "xhs" and d.get("url"):
                xhs.remember(d["aweme_id"], d["url"])

        if download_only:
            had = [0]
            subdir = _safe_name(folder_name or folder, 60) if folder else ""
            base = config.download_dir(platform)
            target = base / subdir if subdir else base

            def on_dl(d):
                seen(d)
                if downloaded_path(d["aweme_id"], target):
                    had[0] += 1
                    known[0] += 1
                    return
                known[0] = 0
                if downloaded_path(d["aweme_id"], target):
                    had[0] += 1
                    return
                media = {k: d.get(k) for k in ("hq_urls", "play_urls", "images", "desc", "is_note", "title",
                                               "author", "url", "is_ad")}
                if self._queue_download(d.get("url") or d["aweme_id"], aweme_id=d["aweme_id"], title=d.get("title", ""),
                                        author=d.get("author", ""), kind="note" if d.get("is_note") else "video",
                                        subdir=subdir, media=media, platform=platform):
                    added[0] += 1

            b = self._browser(platform)
            if folder:
                n = b.sync_folder(folder, limit, on_dl, should_stop=stop_now)
            else:
                n = b.sync_list(kind, limit, on_dl, should_stop=stop_now)
            self._save_favorites(platform, rows, subdir)
            self.log(f"读到 {n} 条：{added[0]} 条加入下载队列" + (f"，{had[0]} 条之前已经下载过，跳过" if had[0] else "") + "。")
            if not self.stopping():
                self._download_all()
            return

        # 注意：删掉过的条目，同步收藏时照样加回来（只有「代刷」会跳过删掉过的）
        def on_item(d):
            seen(d)
            # 以前从本地文件夹导入的条目没有作品 ID：按下载记录里的文件位置认出来，算「已经有」，顺手补上 ID
            lp = downloaded_path(d["aweme_id"])
            item_id, new = db.add("favorite", aweme_id=d["aweme_id"], url=d["url"], title=d["title"],
                                  author=d["author"], duration=d["duration"], cover=d["cover"], platform=platform,
                                  local_path=lp)
            if not new and lp:
                old_row = db.get(item_id)
                if old_row and not old_row.get("aweme_id"):
                    db.update(item_id, aweme_id=d["aweme_id"], url=old_row.get("url") or d["url"])
            ft = d.get("fav_time")
            if new and ft:
                db.update(item_id, fav_at=ft)  # 抖音给了真实收藏时间
            if since:
                if ft:
                    past[0] = 1 if ft < since else 0
                elif not new:
                    r0 = db.get(item_id) or {}
                    # 从旧的本地库导入的（source=local）：导入时间不是收藏时间，肯定是以前就收藏的，算「早于 30 天」
                    seen_at = 0 if r0.get("source") == "local" else (
                        r0.get("fav_at") or r0.get("created_at") or time.time())
                    past[0] = past[0] + 1 if seen_at < since else 0
                else:
                    past[0] = 0
            if folder:
                db.update(item_id, fav_folder=_safe_name(folder_name or folder, 60))
            known[0] = 0 if new else known[0] + 1
            if new:
                new_ids.append(item_id)
                added[0] += 1
                if d.get("is_note"):
                    db.update(item_id, media_type="note")
                    notes_n[0] += 1
            else:
                old = db.get(item_id)
                retry_note = (old and old["status"] == "skipped" and "图文" in (old.get("reason") or ""))
                if old and (old["status"] == "failed" or retry_note):  # 之前失败的 / 以前跳过的图文，这次重新处理
                    db.update(item_id, status="queued", attempts=0, reason=None,
                              media_type="note" if (d.get("is_note") or retry_note) else old.get("media_type"))
                    requeued[0] += 1

        b = self._browser(platform)
        if folder:
            n = b.sync_folder(folder, limit, on_item, should_stop=stop_now)
        else:
            n = b.sync_list(kind, limit, on_item, should_stop=stop_now)
        self._save_favorites(platform, rows)
        msg = f"{pname}：读到 {n} 条，其中新增 {added[0]} 条" + (f"（含图文 {notes_n[0]} 条）" if notes_n[0] else "")
        if requeued[0]:
            msg += f"，之前失败的 {requeued[0]} 条重新排队"
        self.log(msg + "（已完成或已跳过的不会重复处理）。")
        if then_process and not self.stopping():
            self._process_all()
        return new_ids

    def login_pending(self):
        """有平台刚检查出没登录、正等着扫码：先别关它的浏览器窗口。"""
        return any(v.get("ok") is False and time.time() - v.get("t", 0) < 1800 for v in self.login.values())

    def _login_hint(self, p):
        """没登录时给用户的说明；小红书写清楚窗口现在在哪个站。"""
        if p != "xhs":
            return "没登录或登录过期，请在弹出的浏览器窗口里扫码登录"
        c = getattr(self.browsers.get("xhs"), "last_check", None) or {}
        site, has = c.get("site", "xiaohongshu.com"), c.get("has") or {}
        if site == "rednote.com":
            tip = "窗口现在在 rednote.com（小红书海外站，你的网络被跳过去了），这个站要单独登录"
            if has.get("xiaohongshu.com"):
                tip += "；你在 xiaohongshu.com 的登录它不认"
            return tip + "。请扫这个窗口里的二维码（用小红书 App 就行），扫完等页面显示头像再点「我登好了」"
        tip = "窗口在 xiaohongshu.com，没登录或登录过期"
        if has.get("rednote.com"):
            tip += "（rednote.com 那边有登录，但网络没被跳过去，用不上）"
        return tip + "。请扫窗口里的二维码，扫完等页面显示头像再点「我登好了」"

    def _login_check(self, platforms, reopen=False):
        """整理前先看登录状态：每个平台只检查一次、不等待。没登录的窗口留在最前面、打开扫码，
        等用户在页面上点「我登好了」再检查一次。"""
        for p in platforms:
            self.login[p] = {"ok": None, "t": time.time(), "msg": "检查中…"}
        for p in platforms:
            name = config.PLATFORMS[p]
            if reopen:  # 窗口不小心关了：把旧的彻底关掉，重新开一个
                self._close_browser(only=p)
                self.log(f"重新打开{name}浏览器窗口……")
            self.state["stage"] = f"检查{name}登录…"
            ok, err = False, ""
            for attempt in (1, 2):  # 窗口被手动关掉时，第一次会报错，重开一次
                try:
                    ok = self._browser(p).check_login(prompt=True)
                    err = ""
                    break
                except Exception as e:
                    err = str(e).splitlines()[0][:160] if str(e) else type(e).__name__
                    self._close_browser(only=p)
            if err:
                self.login[p] = {"ok": False, "t": time.time(), "msg": f"浏览器出错：{err}"}
                self.log(f"⚠ {name}登录检查出错：{err}")
            elif ok:
                site = (getattr(self.browsers.get(p), "last_check", None) or {}).get("site") if p == "xhs" else ""
                self.login[p] = {"ok": True, "t": time.time(), "msg": f"已登录（{site}）" if site else "已登录"}
                self.log(f"{name}：已登录。")
            else:
                self.login[p] = {"ok": False, "t": time.time(), "msg": self._login_hint(p)}
                self.log(f"{name}：登录过期了，已打开登录窗口，扫码登录后点「我登好了」。")

    def _topic_feed(self, topic_id, keyword, n, platforms, process=True):
        """专题「代刷」：用抖音 / 小红书自己的搜索搜关键词，各拿 n 条学习库里还没有的视频，
        加进这个专题，再走完整流程（下载 → 转文字 → 润色）收进学习库。一个平台一个平台来，不并发。"""
        from . import topics
        s = config.load()
        max_sec = (s.get("max_minutes") or 15) * 60 if s.get("skip_long", True) else 0
        ign = ignored_ids()
        self.log(f"代刷「{keyword}」：{'、'.join(config.PLATFORMS[p] for p in platforms)}{'各' if len(platforms) > 1 else ''}找 {n} 条新视频……")
        new_ids, old_ids = [], []
        for p in platforms:
            if self.stopping():
                break
            got = []

            def on_item(d, p=p, got=got):
                if len(got) >= n or d.get("is_note") or d.get("is_ad") or is_ignored(ign, p, d["aweme_id"]):
                    return
                if max_sec and (d.get("duration") or 0) > max_sec:  # 太长的不要（抖音给时长，小红书搜索结果不给）
                    return
                iid, new = db.add("search", aweme_id=d["aweme_id"], url=d["url"], title=d["title"],
                                  author=d["author"], duration=d.get("duration"), cover=d.get("cover"), platform=p)
                if new:
                    got.append(iid)
                else:
                    old_ids.append(iid)  # 库里已经有的：也放进专题，但不算在 n 条里

            self.state["stage"] = f"代刷：在{config.PLATFORMS[p]}搜「{keyword}」"
            try:
                # 多次代刷：已有的、删过的都跳过，一直往下翻，直到凑够 n 条新的或者搜索结果到底（最多看 300 条）
                self._browser(p).search(keyword, limit=300, on_item=on_item,
                                        should_stop=lambda got=got: self.stopping() or len(got) >= n)
            except douyin.DouyinError as e:
                self.log(f"⚠ {config.PLATFORMS[p]}搜索没完成：{e}")
            finally:
                if p == "douyin":
                    self._close_browser(only="douyin")
            self.log(f"{config.PLATFORMS[p]}：找到 {len(got)} 条新视频" + ("（不够 n 条：搜索结果里新的视频不多）" if len(got) < n else "") + "。")
            new_ids += got
        added = topics.items_add(topic_id, new_ids + old_ids)
        self.log(f"已放进专题 {added} 条（新视频 {len(new_ids)} 条，学习库里本来就有的 {len(old_ids)} 条）。")
        if new_ids:
            self.force_extract |= set(new_ids)  # 不管「只转文字」模式，都润色进学习库
            _save_force(self.force_extract)
            self.priority |= set(new_ids)  # 代刷的插队：下一条就处理它们
            _save_force(self.priority, PRIORITY_PATH)
        if not process:  # 插队搜的：交给正在跑的队列 / 排一个处理任务，搜完就把浏览器让回去
            if new_ids:
                self.submit("process", ids=new_ids)
            self.log(f"代刷「{keyword}」搜完了，{len(new_ids)} 条新视频排进处理队列。")
            return
        if new_ids and not self.stopping():
            self._process_all(new_ids)
        self.log(f"✓ 代刷「{keyword}」完成。刷新学习库就能在专题里看到。")

    def _weekly_sync(self, platforms, days=7, full=False):
        """每周增量整理：读各平台「收藏」最前面的新收藏（碰到连续 20 条已有的就停），下载进本地视频库、
        转文字、按保留原话的方式润色排版，最后生成这一周的周报。"""
        s = config.load()
        self.log(f"开始整理过去 {days} 天的{'全部' if full else '新'}收藏：{'、'.join(config.PLATFORMS[p] for p in platforms)}"
                 + ("（完整过一遍，不在碰到已有的就停）" if full else ""))
        since = time.time() - days * 86400
        ids = []
        for p in platforms:
            if self.stopping():
                break
            try:
                if full:
                    ids += self._sync("collection", 5000, False, platform=p, since=since) or []
                else:
                    ids += self._sync("collection", int(s.get("sync_limit") or 200), False, platform=p,
                                      known_stop=20) or []
            except douyin.DouyinError as e:
                self.log(f"⚠ {config.PLATFORMS[p]}同步没完成：{e}")
            finally:
                if p == "douyin":
                    self._close_browser(only="douyin")
        if self.stopping():
            self.log(f"已停止整理（已读到的 {len(ids)} 条新收藏留在队列里，下次整理接着做）。")
            return
        config.save({"last_monthly_sync" if full else "last_weekly_sync": time.time()})
        # 这一周收进来、但上次整理被打断还在排队的，也一起处理（同步时它们已经不算「新增」了）
        marks = ",".join("?" * len(platforms))
        for r in db.conn().execute(
                f"SELECT id FROM items WHERE status='queued' AND platform IN ({marks}) "
                f"AND COALESCE(fav_at, created_at) >= ?", (*platforms, since)).fetchall():
            if r["id"] not in ids:
                ids.append(r["id"])
        if ids and not self.stopping():
            self.force_extract |= set(ids)  # 每周整理：不管是不是「只转文字」模式，都润色进学习库
            _save_force(self.force_extract)
            self._process_all(ids)
        if self.stopping():
            self.log("已停止整理：转写 / 润色到一半的，下次整理会接着做。")
            return
        try:
            from . import weekly
            weekly.save_md(0)
        except Exception as e:
            self.log(f"⚠ 周报没生成：{e}")
        self.log(f"✓ 这一周的收藏整理完了：新增 {len(ids)} 条。")

    # ------------------------------------------------------------ 批量处理
    def _next(self, ids=None, exclude=(), local_only=False, prefer=None):
        """下一条要处理的：先拿插队的（代刷来的，按加入顺序），没有再按原来的顺序。"""
        if self.priority:
            for i in sorted(self.priority):
                if i in exclude or (ids is not None and i not in ids):
                    continue
                it = db.get(i)
                if it and it["status"] == "queued" and (not local_only or it.get("source") == "local"):
                    return it
                if not it or it["status"] not in ("queued", "downloading", "transcribing", "extracting", "fetching"):
                    self.priority.discard(i)  # 处理完 / 删掉了的，不再记
                    _save_force(self.priority, PRIORITY_PATH)
        return db.next_queued(ids, exclude=exclude, local_only=local_only, prefer=prefer)

    def _process_all(self, ids=None):
        """处理队列。ids：只处理这几条（比如刚从本地文件夹导入的），队列里别的条目不动。"""
        self.retry_later = []
        total = (db.count_queued(ids) if ids is not None else db.stats()["by_status"].get("queued", 0))
        self.state.update(done=0, total=total)
        if not self.state["total"]:
            self.log("队列里没有待处理的视频。")
            return
        n = max(1, int(config.load().get("asr_workers") or 1))
        self.log(f"开始处理，共 {self.state['total']} 条" + (f"，{n} 路并行" if n > 1 else "") + "。")
        while not self.stopping():
            if n > 1:
                self._run_parallel(ids, n)
                it = None
            else:
                it = self._next(ids)
            if it is None:
                if self.retry_later:
                    ids, self.retry_later = self.retry_later, []
                    self.log(f"有 {len(ids)} 条失败了，30 秒后自动重试一次……")
                    for _ in range(30):
                        if self.stopping():
                            return
                        time.sleep(1)
                    for i in ids:
                        db.update(i, status="queued")
                    self.state["total"] += len(ids)
                    continue
                break
            self._service_urgent()
            self.state.update(item_id=it["id"])
            self._process_one(it)
            self.state["done"] += 1
        self.log("这一批处理完了。" if not self.stopping() else "已停止。")

    def _run_parallel(self, ids, n):
        """n 个线程一起处理队列：本地文件并行转写；在线视频（要用浏览器）只由 0 号线程一条一条处理。"""
        lock, claimed = threading.Lock(), set()
        roots = [r for r in (config.load().get("asr_lane_roots") or []) if r]
        if ids is not None:
            ids = list(ids)

        def loop(k):
            while not self.stopping():
                if k == 0:
                    self._service_urgent()
                with lock:
                    it = self._next(ids, exclude=claimed, local_only=k > 0,
                                    prefer=roots[k % len(roots)] if roots else None)
                    if it is None:
                        return
                    claimed.add(it["id"])
                self.state.update(item_id=it["id"])
                try:
                    self._process_one(it)
                finally:
                    with lock:
                        claimed.discard(it["id"])
                        self.state["done"] += 1

        # 0 号（会用到浏览器的那一路）就在 worker 线程里跑：浏览器窗口只能在打开它的线程里用，
        # 这样登录检查、同步收藏、读详情始终是同一个线程
        ts = [threading.Thread(target=loop, args=(k,), daemon=True, name=f"asr{k}") for k in range(1, n)]
        for t in ts:
            t.start()
        loop(0)
        while any(t.is_alive() for t in ts):  # 其它几路还在转写：这期间有登录检查也能马上做
            self._service_urgent()
            for t in ts:
                t.join(0.5)

    def _stage(self, it, status, msg):
        db.update(it["id"], status=status, reason=None)
        self.prog.stage(status)
        self.state["stage"] = msg
        self.log(f"[{it['id']}] {msg}")

    def _process_one(self, it):
        s = config.load()
        self.prog.begin(it, bool(it.get("transcript")), transcribe.pick_engine(s.get("asr_engine", "auto")))
        try:
            self._process_one_inner(it, s)
        finally:
            cur = db.get(it["id"])
            self.prog.end(ok=bool(cur and cur["status"] == "done"))
            if it["id"] in self.force_extract and (not cur or cur["status"] not in ("queued", "failed")):
                self.force_extract.discard(it["id"])  # 处理完了（进库 / 跳过）就不用再记
                _save_force(self.force_extract)

    def _process_one_inner(self, it, s):
        label = (it.get("title") or it.get("url") or "")[:30]
        # 「只转文字」模式（设置里 auto_extract=False）：新转好的先停在「待提炼」，等用户点「提炼」。
        # 已经有文字稿的（用户点了「提炼 / 重新提炼」）照常提炼。
        force = (it.get("reason") or "").startswith(FORCE_MARK)
        extract_now = (bool(it.get("transcript")) or s.get("auto_extract", True) or it["id"] in self.force_extract
                       or force)
        try:
            # 1-2. 拿到视频文件（已经有文字稿的跳过这一步，比如重新提炼）
            if not it.get("transcript"):
                media = self._get_media(it, s)
                if media is None:
                    return
                if isinstance(media, Path) and s.get("skip_long", True) and s.get("max_minutes") and not force:
                    # 超过「只转录前 N 分钟」的长视频：整条跳过，留着以后单独处理（不删文件）
                    dur = it.get("duration") or transcribe.media_duration(media)
                    if dur and dur > s["max_minutes"] * 60:
                        db.update(it["id"], status="skipped", duration=dur,
                                  reason=f"超过 {s['max_minutes']} 分钟的长视频，暂不转写，已单独存放")
                        self.log(f"[{it['id']}] 跳过：长视频（{dur / 60:.0f} 分钟），文件保留")
                        return
                if isinstance(media, dict) and "note" in media:  # 图文：下载图片 + 识别图片文字
                    if not self._process_note(it, media["note"]):
                        return
                    it = db.get(it["id"])
                    media = None
                elif isinstance(media, dict) and "note_dir" in media:  # 已经下载到本地的图文文件夹
                    if not self._process_local_note(it, media["note_dir"]):
                        return
                    it = db.get(it["id"])
                    media = None
                elif isinstance(media, dict):  # 拿到了视频自带字幕，不用下载和转写
                    db.update(it["id"], transcript=media["text"], segments=media["segments"] or None)
                    it = db.get(it["id"])
                    media = None
            if not it.get("transcript"):
                self._stage(it, "transcribing", f"转文字：{label}")
                try:
                    tr = transcribe.transcribe(
                        media, engine=s.get("asr_engine", "auto"), mlx_model=s.get("mlx_model"),
                        size=s.get("whisper_model", "small"), use_mirror=s.get("use_hf_mirror", False),
                        max_minutes=0 if force else s.get("max_minutes", 15), log=self.log, should_stop=self.stopping,
                        on_progress=self.prog.set_frac, workers=s.get("asr_workers", 1))
                except transcribe.NoSpeech as e:
                    self._cleanup(it, media, s)
                    if force:  # 用户就是要这条：没说话也收进学习库，看原视频
                        self._keep_forced(it, "", f"没有说话内容（{e}）", label)
                        return
                    db.update(it["id"], status="skipped", reason=str(e))
                    self.log(f"[{it['id']}] 跳过：{e}")
                    self._cleanup(it, media, s)
                    return
                self._cleanup(it, media, s)
                if self.stopping():
                    db.update(it["id"], status="queued")
                    return
                db.update(it["id"], transcript=tr["text"], segments=tr["segments"],
                          duration=it.get("duration") or tr["duration"])
                it = db.get(it["id"])

            # 3. AI 提炼
            if not extract_now:
                db.update(it["id"], status="need_key", reason="已转成文字（只转文字模式），点「提炼」生成笔记")
                self.log(f"[{it['id']}] ✓ 已转成文字，等你点「提炼」：{label}")
                return
            if not (config.llm_cfg(s, "main").get("api_key") or "").strip():
                db.update(it["id"], status="need_key", reason="已转成文字，填好 AI Key 后点「重新提炼」生成笔记")
                self.log(f"[{it['id']}] 已转成文字；还没填 AI Key，先不提炼。")
                return
            if s.get("refine_mode", "yuedu") == "yuedu":
                self._stage(it, "extracting", f"润色排版：{label}")
                self._polish(it, s, label, force=force)
                return
            self._stage(it, "extracting", f"AI 提炼：{label}")
            note, used = llm.extract(s, it.get("title"), it.get("author"), it["transcript"],
                                     kind="note" if it.get("media_type") == "note" else "video")
            config.add_tokens(used)
            if note.get("skip"):
                db.update(it["id"], status="skipped", reason=note.get("skip_reason"), note=None)
                self.log(f"[{it['id']}] 跳过：{note.get('skip_reason')}")
                return
            if not note["relevant"] and s.get("only_relevant", True) and it["source"] != "paste":
                notes.remove(it)
                db.update(it["id"], status="irrelevant", note=note, category=note["category"], md_path=None,
                          reason="和你设置的关注主题无关，已收起")
                self.log(f"[{it['id']}] 和关注主题无关，已收起：{note['title']}")
                return
            it["note"] = note
            path = notes.write(it)
            db.update(it["id"], status="done", note=note, category=note["category"], md_path=path, reason=None)
            self.log(f"[{it['id']}] ✓ 完成：{note['title']}")

        except douyin.NotLoggedIn as e:
            self._fail(it, f"{e}。请点「登录{config.PLATFORMS.get(it.get('platform') or 'douyin')}」后重试", retry=False)
        except (douyin.DouyinError, llm.LLMError) as e:
            self._fail(it, str(e), retry=getattr(e, "retry", isinstance(e, douyin.DouyinError)))
        except Exception as e:
            traceback.print_exc()
            self._fail(it, f"意外错误：{e}", retry=True)

    def _keep_forced(self, it, raw, why, label):
        res = yuedu.forced_keep(it, raw, why)
        it["note"] = res
        try:
            path = notes.write(it)
        except Exception:
            path = None
        db.update(it["id"], status="done", note=res, category=res["tab"], md_path=path, reason=None)
        self.log(f"[{it['id']}] ✓ 按你的选择收进学习库（{why or '原文'}）：{label}")

    def _polish(self, it, s, label, force=False):
        """保留原话的润色排版（video-yuedu 规则）：keep 进学习库；没信息量 / 其他 单独列出，不删。
        代刷来的：顺便判断和专题有没有关系，完全无关的拿出专题、放进回收站。"""
        tps = []
        if it.get("source") == "search":
            tps = [dict(r) for r in db.conn().execute(
                "SELECT t.id, t.name FROM topic_items ti JOIN topics t ON t.id=ti.topic_id WHERE ti.item_id=?", (it["id"],))]
        res, used = yuedu.polish(s, it, log=self.log, force=force,
                                 topic=" / ".join(t["name"] for t in tps) or None)
        config.add_tokens(used)
        if tps and res.get("topic_fit") is False:
            self._drop_unrelated(it, res, tps, label)
            return
        if force and res["verdict"] != "keep":
            self._keep_forced(it, it.get("transcript"), f"AI 判成{res.get('low_info_type') or '其他'}，按你的选择照原文收", label)
            return
        if res["verdict"] == "low_info":
            db.update(it["id"], status="skipped", note=res, reason=f"没信息量：{res.get('low_info_type')}"
                      + (f"（{res['reason']}）" if res.get("reason") else ""))
            self.log(f"[{it['id']}] 没信息量（{res.get('low_info_type')}），单独列出：{label}")
            return
        if res["verdict"] == "move":
            db.update(it["id"], status="irrelevant", note=res, category=yuedu.OTHER,
                      reason="其他（影视剧台词 / 时事 / 广告等），不整理" + (f"：{res['reason']}" if res.get("reason") else ""))
            self.log(f"[{it['id']}] 归到「其他」，不整理：{label}")
            return
        it["note"] = res
        try:
            path = notes.write(it)
        except Exception:
            path = None
        db.update(it["id"], status="done", note=res, category=res["tab"], md_path=path, reason=None)
        pct = res.get("change_pct")
        self.log(f"[{it['id']}] ✓ {res['tab']} / {res['category']} / {res['topic']}：{res['theme']}"
                 + (f"（改动 {pct:.0f}%）" if pct is not None else ""))

    def _drop_unrelated(self, it, res, tps, label):
        """代刷搜偏了（AI 判断和专题完全无关）：从专题里拿掉，直接进回收站（在回收站里可以恢复或彻底删除）。"""
        from . import reader_build, topics
        for t in tps:
            topics.items_remove(t["id"], [it["id"]])
        if res["verdict"] == "keep":  # 正文照常存好（回收站里恢复后就能看）
            it["note"] = res
            try:
                path = notes.write(it)
            except Exception:
                path = None
            db.update(it["id"], status="done", note=res, category=res["tab"], md_path=path, reason=None)
        else:
            db.update(it["id"], status="skipped", note=res, reason="代刷搜到的，和专题无关")
        reader_build.save_deleted(sorted(set(reader_build.deleted_ids()) | {it["id"]}))
        self.log(f"[{it['id']}] 和专题「{' / '.join(t['name'] for t in tps)}」无关，拿出专题、放进回收站：{label}")

    def _fail(self, it, msg, retry):
        cur = db.get(it["id"]) or it
        attempts = (cur.get("attempts") or 0) + 1
        # 一直读不到视频信息：多半是视频被删了或设成私密，试过 3 次就挪到「已跳过」，不再一直挂在失败里
        prev = cur.get("reason") or ""
        if "没读到视频信息" in msg and ("没读到视频信息" in prev or attempts >= 3) and attempts >= 2:
            db.update(it["id"], status="skipped", attempts=attempts,
                      reason="视频已删除或设为私密（试了几次都读不到视频信息）；点「原始链接」可以确认")
            self.log(f"[{it['id']}] 读不到视频信息，可能已删除 / 私密，挪到「已跳过」")
            return
        db.update(it["id"], status="failed", reason=msg, attempts=attempts)
        self.log(f"[{it['id']}] ✗ 失败：{msg}")
        if retry and attempts < 2:
            self.retry_later.append(it["id"])

    def _get_media(self, it, s):
        """返回本地视频路径；视频自带字幕时返回 {"text", "segments"}；不需要处理时返回 None（已标记跳过）。"""
        if it["source"] == "local":
            p = Path(it["local_path"] or "")
            if p.exists() and it["local_path"]:
                self.prog.set_title(it.get("title"), it.get("duration"))
                return {"note_dir": p} if p.is_dir() else p
            if not it.get("aweme_id"):
                raise douyin.DouyinError("本地文件不见了，也没有原始链接，没法重新下载")
            # 文件被挪走 / 删了，但知道是哪条收藏：按原始链接重新下载（下面的在线流程，一条一条来）
            self.log(f"[{it['id']}] 本地文件不见了，按原始链接重新下载：{(it.get('title') or '')[:30]}")

        aweme_id = it.get("aweme_id")
        platform = it.get("platform") or config.platform_of(aweme_id or "", it.get("url") or "")
        mod = self._mod(platform)
        # 本地视频库里已经有的作品：直接用本地文件，不再打开网页、不重复下载
        local = downloaded_path(aweme_id) if aweme_id else None
        if local:
            p = Path(local)
            self.log(f"[{it['id']}] 之前下载过，直接用本地文件：{p.name}")
            db.update(it["id"], local_path=str(p))
            if p.is_dir():
                db.update(it["id"], media_type="note")
                it["media_type"] = "note"
                return {"note_dir": p}
            return p
        if not aweme_id:
            self._stage(it, "fetching", f"解析链接：{it['url'][:40]}")
            aweme_id, kind = mod.resolve(it["url"])
            if kind == "note":
                db.update(it["id"], media_type="note")
                it["media_type"] = "note"
            other = db.conn().execute("SELECT id FROM items WHERE aweme_id=? AND id<>?",
                                      (aweme_id, it["id"])).fetchone()
            if other:
                db.delete(it["id"])
                self.log(f"[{it['id']}] 这个视频之前已经加过了（第 {other['id']} 条），不重复处理。")
                return None
            db.update(it["id"], aweme_id=aweme_id)
            it["aweme_id"] = aweme_id

        kind = "note" if it.get("media_type") == "note" else "video"
        self._stage(it, "fetching", f"读取{'图文' if kind == 'note' else '视频'}信息：{aweme_id}")
        d = self._detail(platform, aweme_id, kind, it.get("url"))
        b = self._browser(platform)
        if not d.get("is_note") and it.get("media_type") == "note":  # 小红书链接分不出图文/视频，读到详情再改
            db.update(it["id"], media_type="video")
            it["media_type"] = "video"
        db.update(it["id"], title=d.get("title") or it.get("title"), author=d.get("author") or it.get("author"),
                  duration=d.get("duration") or it.get("duration"), cover=d.get("cover") or it.get("cover"),
                  url=d.get("url") or it.get("url"))
        it.update({k: v for k, v in d.items() if v and k in ("title", "author", "duration", "cover", "url")})
        self.prog.set_title(it.get("title"), d.get("duration"))
        if d.get("is_ad"):
            db.update(it["id"], status="skipped", reason="广告视频")
            self.log(f"[{it['id']}] 跳过：广告")
            return None
        if d.get("is_note"):
            db.update(it["id"], media_type="note")
            it["media_type"] = "note"
            if not d.get("images"):
                db.update(it["id"], status="skipped", reason="这条图文没有读到图片")
                return None
            return {"note": d}

        if s.get("use_subtitles", True) and d.get("subtitle_urls"):
            for su in d["subtitle_urls"]:
                try:
                    raw = b.fetch_text(su)
                    text = transcribe.parse_subtitle(raw)
                except Exception:
                    continue
                if len(text) >= 30:
                    self.log(f"[{it['id']}] 视频自带字幕，直接使用（跳过下载和转写）")
                    return {"text": text, "segments": transcribe.subtitle_segments(raw)}

        self._stage(it, "downloading", f"下载视频：{(it.get('title') or '')[:30]}")
        urls = d.get("play_urls") or d.get("hq_urls") or []
        if s.get("keep_video", True):
            # 本地视频库：存进「下载位置 / 收藏夹分类」，文件名「作者 - 标题.mp4」，以后随时能看
            out = config.download_dir(platform)
            if it.get("fav_folder"):
                out = out / it["fav_folder"]
            out.mkdir(parents=True, exist_ok=True)
            name = _safe_name(f"{it.get('author') or config.PLATFORMS[platform]} - {it.get('title') or aweme_id}")
            with _name_lock:
                dest = _unique(out, name, ".mp4")
                dest.touch()
            try:
                _http_file(urls, dest)
            except Exception:
                dest.unlink(missing_ok=True)
                raise
            mark_downloaded(aweme_id, dest)
            db.update(it["id"], local_path=str(dest))
        else:
            dest = config.MEDIA_DIR / f"{aweme_id}.mp4"
            b.download(urls, dest)
        time.sleep(1.0)  # 别太快，降低被风控的概率
        return dest

    def _process_note(self, it, d):
        """图文：下载图片到笔记目录，识别图片里的文字，和正文合成文字稿。返回 False 表示已标记跳过。"""
        aweme_id = it.get("aweme_id") or str(it["id"])
        imgs = d.get("images") or []
        self.prog.mark_note(len(imgs))
        self._stage(it, "downloading", f"下载图文图片（{len(imgs)} 张）：{(it.get('title') or '')[:24]}")
        lib = config.library_dir()
        folder = lib / notes.IMAGE_DIR / aweme_id
        files = self._browser(it.get("platform") or "douyin").download_images(imgs, folder)
        return self._ocr_note(it, files, d.get("desc"))

    def _process_local_note(self, it, src: Path):
        """已经下载到本地的图文文件夹（图片 + 正文.txt）：把图片复制进笔记目录（原文件不动），再识别文字。"""
        imgs = sorted(p for p in src.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT)
        if not imgs:
            db.update(it["id"], status="skipped", reason="这个图文文件夹里没有图片")
            self.log(f"[{it['id']}] 跳过：图文文件夹里没有图片")
            return False
        self.prog.mark_note(len(imgs))
        self._stage(it, "downloading", f"读取本地图文（{len(imgs)} 张）：{(it.get('title') or '')[:24]}")
        lib = config.library_dir()
        folder = lib / notes.IMAGE_DIR / (it.get("aweme_id") or str(it["id"]))
        folder.mkdir(parents=True, exist_ok=True)
        files = []
        for p in imgs:
            dst = folder / p.name
            if not dst.exists():
                shutil.copy2(p, dst)
            files.append(dst)
        desc = ""
        txt = src / "正文.txt"
        if txt.exists():
            desc = txt.read_text(encoding="utf-8", errors="ignore")
        return self._ocr_note(it, files, desc)

    def _ocr_note(self, it, files, desc):
        lib = config.library_dir()
        rels = [f.relative_to(lib).as_posix() for f in files]
        db.update(it["id"], images=rels, cover="/files/" + rels[0])

        self._stage(it, "transcribing", f"识别图片文字（{len(files)} 张）")
        parts = []
        desc = (desc or it.get("title") or "").strip()
        if desc:
            parts.append("【正文】\n" + desc)
        if ocr.backend() is None:
            self.log(f"[{it['id']}] ⚠ 没装文字识别组件，只用正文。重新运行一次安装脚本就能识别图片里的字。")
        else:
            for i, f in enumerate(files, 1):
                if self.stopping():
                    break
                try:
                    text = ocr.recognize(f)
                except Exception as e:
                    self.log(f"[{it['id']}] 第 {i} 张图识别失败：{str(e)[:80]}")
                    text = ""
                if text:
                    parts.append(f"【图{i}】\n{text}")
                self.prog.set_frac(i / len(files))
        transcript = "\n\n".join(parts)
        if len(transcript.replace("【正文】", "").replace(" ", "").replace("\n", "")) < 15:
            db.update(it["id"], status="skipped", reason="图文里几乎没有文字（可能是纯图片、表情包或摄影作品）")
            self.log(f"[{it['id']}] 跳过：图文里几乎没有文字")
            return False
        db.update(it["id"], transcript=transcript, segments=None)
        self.log(f"[{it['id']}] 图文文字已提取：{len(files)} 张图，共 {len(transcript)} 字")
        return True

    # ------------------------------------------------------------ 仅下载
    PARALLEL = 1  # 有现成地址的条目同时下几个（用户 2026-11-08：并发下载容易被封号，只一条一条下）

    def _download_all(self):
        """「仅下载」队列：有列表地址的用普通 HTTP 同时下 3 个；失败的再交给浏览器打开作品页重试。"""
        streak, retried = [0], False
        in_flight = {}
        with concurrent.futures.ThreadPoolExecutor(self.PARALLEL, thread_name_prefix="dl") as ex:
            while True:
                while len(in_flight) < self.PARALLEL and not self.stopping():
                    job = next((d for d in self.downloads if d["status"] == "queued"), None)
                    if job is None:
                        break
                    job["status"] = "running"
                    in_flight[ex.submit(self._fast_download, job)] = job
                    time.sleep(random.uniform(0.3, 0.8))  # 错开一点，别同时发起
                if not in_flight:
                    if self.stopping():
                        break
                    # 全部跑完后，失败的自动再试一次（网络抖动、地址过期等）
                    again = [d for d in self.downloads if d["status"] == "failed" and not d.get("retried")]
                    if retried or not again:
                        break
                    retried = True
                    self.log(f"[下载] 有 {len(again)} 条失败，30 秒后自动重试一次……")
                    if self._sleep(30):
                        break
                    for d in again:
                        d.update(status="queued", error="", retried=True, media=None)  # 重试时打开作品页拿新地址
                    continue
                done, _ = concurrent.futures.wait(list(in_flight), timeout=2,
                                                  return_when=concurrent.futures.FIRST_COMPLETED)
                for f in done:
                    job = in_flight.pop(f)
                    try:
                        r = f.result()
                    except Exception:
                        r = "slow"
                    if r == "slow":  # 快速通道不行：在这个线程里用浏览器慢慢来
                        try:
                            r = self._download_one(job)
                        except Exception as e:
                            self._dl_failed(job, e, streak)
                            if streak[0] >= 5:  # 连续失败多半是被风控或断网了，歇一会儿再继续
                                self.log("[下载] 连续 5 条失败，可能被限流或网络断了，暂停 3 分钟后继续……")
                                self._close_browser(keep="xhs")
                                if self._sleep(180):
                                    break
                                streak[0] = 0
                            continue
                    self._dl_ok(job, r, streak)
                c = self.download_counts()
                cur = next(iter(in_flight.values()), None)
                if cur:
                    where = f"{cur['subdir']} · " if cur.get("subdir") else ""
                    self.state.update(stage=f"仅下载（{where}还剩 {c['queued'] + c['running']} 条）："
                                            f"{(cur.get('title') or cur['url'])[:30]}")
            for f in in_flight:  # 被停止：等正在下的那几条收尾
                f.cancel()
        for d in self.downloads:  # 被停止：没开始的标记一下，可以再点重试
            if d["status"] in ("queued", "running"):
                d.update(status="failed", error="已停止")

    def _dl_ok(self, job, r, streak):
        job["media"] = None  # 下完就不留地址了，省内存
        if r == "skipped":
            job["status"] = "skipped"
            return
        job["status"] = "done"
        streak[0] = 0
        where = f"{job['subdir']} · " if job.get("subdir") else ""
        tag = {"copied": "⧉ 复制", "moved": "→ 归类"}.get(r, "✓")
        self.log(f"[下载] {tag} {where}{Path(job['path']).name}")

    def _dl_failed(self, job, e, streak):
        msg = str(e) if isinstance(e, douyin.DouyinError) else f"出错了：{e}"
        job.update(status="failed", error=msg[:200], media=None)
        where = f"{job['subdir']} · " if job.get("subdir") else ""
        self.log(f"[下载] ✗ {where}{(job.get('title') or job['url'])[:40]}：{msg[:150]}")
        if not isinstance(e, douyin.DouyinError):
            traceback.print_exc()
        streak[0] += 1

    def _fast_download(self, job):
        """在下载线程里跑：只用文件操作和普通 HTTP，不碰浏览器。做不了的返回 "slow"。"""
        aweme_id = job.get("aweme_id")
        media = job.get("media") or {}
        if not aweme_id or not (media.get("hq_urls") or media.get("play_urls") or media.get("images")):
            return "slow"
        out = _job_dir(job)
        out.mkdir(parents=True, exist_ok=True)
        if downloaded_path(aweme_id, out) or downloaded_path(aweme_id):
            return "slow"  # 跳过 / 归类 / 复制都很快，交给原来的逻辑处理
        d = media
        job.update(title=d.get("title") or job.get("title") or "", author=d.get("author") or job.get("author") or "",
                   kind="note" if d.get("is_note") else "video")
        name = _safe_name(f"{d.get('author') or config.PLATFORMS[job.get('platform') or 'douyin']} - "
                          f"{d.get('title') or aweme_id}")
        try:
            if d.get("is_note"):
                if not d.get("images"):
                    return "slow"
                with _name_lock:
                    folder = _unique(out, name)
                    folder.mkdir(parents=True)
                try:
                    _http_images(d["images"], folder)
                except Exception:
                    shutil.rmtree(folder, ignore_errors=True)
                    raise
                (folder / "正文.txt").write_text(f"{d.get('desc') or ''}\n\n作者：{d.get('author') or ''}\n"
                                                 f"链接：{d.get('url') or job['url']}\n", encoding="utf-8")
                job["path"] = str(folder)
            else:
                urls = d.get("hq_urls") or d.get("play_urls") or []
                with _name_lock:
                    dest = _unique(out, name, ".mp4")
                    dest.touch()  # 先占住这个文件名，别的线程就不会用同一个
                try:
                    _http_file(urls, dest)
                except Exception:
                    dest.unlink(missing_ok=True)
                    raise
                job["path"] = str(dest)
        except Exception as e:
            self.log(f"[下载] 直连失败（{str(e)[:60]}），改用浏览器重试：{(job.get('title') or '')[:30]}")
            return "slow"
        mark_downloaded(aweme_id, job["path"])
        return None

    def _sleep(self, sec):
        """可被「停止」打断的等待；被打断返回 True。"""
        for _ in range(int(sec)):
            if self.stopping():
                return True
            time.sleep(1)
        return False

    def _download_one(self, job):
        """下载一条。返回 "skipped"（这个文件夹里已经有了）、"copied"（别的文件夹下过，复制过来）或 None（新下载）。"""
        aweme_id = job.get("aweme_id")
        platform = job.get("platform") or "douyin"
        if aweme_id:
            kind = job.get("kind") or "video"
        else:
            aweme_id, kind = self._mod(platform).resolve(job["url"])
            job["aweme_id"] = aweme_id
        out = _job_dir(job)
        out.mkdir(parents=True, exist_ok=True)
        old = downloaded_path(aweme_id, out)
        if old:
            job.update(path=old, error="之前已经下载过")
            return "skipped"
        other = downloaded_path(aweme_id)
        if other and job.get("subdir") and Path(other).parent.resolve() == config.download_dir(platform).resolve():
            # 以前没分文件夹、直接下在下载目录里的：挪进分类文件夹，不重新下载
            src = Path(other)
            dest = _unique(out, src.name) if src.is_dir() else _unique(out, src.stem, src.suffix)
            shutil.move(str(src), str(dest))
            job["path"] = str(dest)
            mark_downloaded(aweme_id, dest, replace=other)
            return "moved"
        if other:  # 同一条收藏在别的分类里下过：直接复制一份，不重复下载
            src = Path(other)
            if src.is_dir():
                dest = _unique(out, src.name)
                shutil.copytree(src, dest)
            else:
                dest = _unique(out, src.stem, src.suffix)
                shutil.copy2(src, dest)
            job["path"] = str(dest)
            mark_downloaded(aweme_id, dest)
            return "copied"

        media = job.get("media") or {}
        if media.get("hq_urls") or media.get("play_urls") or media.get("images"):
            try:
                self._save(job, aweme_id, media, out)
                return None
            except douyin.DouyinError as e:
                # 列表里的地址可能过期了，打开作品页重新拿一次
                self.log(f"[下载] 列表地址下载失败（{str(e)[:60]}），改为打开作品页重试")
        d = self._detail(platform, aweme_id, kind, job.get("url"))
        self._save(job, aweme_id, d, out)
        return None

    def _save(self, job, aweme_id, d, out):
        job.update(title=d.get("title") or job.get("title") or "", author=d.get("author") or job.get("author") or "",
                   kind="note" if d.get("is_note") else "video")
        platform = job.get("platform") or "douyin"
        b = self._browser(platform)
        if not d.get("title") and job.get("title"):  # 网页解析站只给地址，标题用收藏列表里的
            d = {**d, "title": job["title"], "author": d.get("author") or job.get("author")}
        name = _safe_name(f"{d.get('author') or config.PLATFORMS[platform]} - {d.get('title') or aweme_id}")
        if d.get("is_note"):
            if not d.get("images"):
                raise douyin.DouyinError("这条图文没有读到图片")
            folder = _unique(out, name)
            try:
                b.download_images(d["images"], folder)
            except douyin.DouyinError:
                shutil.rmtree(folder, ignore_errors=True)
                raise
            (folder / "正文.txt").write_text(f"{d.get('desc') or ''}\n\n作者：{d.get('author') or ''}\n"
                                             f"链接：{d.get('url') or job['url']}\n", encoding="utf-8")
            job["path"] = str(folder)
        else:
            urls = d.get("hq_urls") or d.get("play_urls") or []
            if not urls:
                raise douyin.DouyinError("没拿到视频地址（可能是直播、已删除或私密视频）")
            dest = _unique(out, name, ".mp4")
            b.download(urls, dest)
            job["path"] = str(dest)
        mark_downloaded(aweme_id, job["path"])

    def _cleanup(self, it, media, s):
        if media is None:
            return
        if it["source"] == "local" or s.get("keep_video"):
            return
        # 只删程序自己临时下载到 data/media 的文件；复用的「仅下载」文件、用户自己的文件一律不动
        try:
            Path(media).resolve().relative_to(config.MEDIA_DIR.resolve())
        except (ValueError, OSError):
            return
        try:
            Path(media).unlink(missing_ok=True)
        except Exception:
            pass


DOWNLOADED_PATH = config.DATA / "downloaded.json"  # 「仅下载」记录：作品 ID → 保存位置
_dl_lock = threading.Lock()
_name_lock = threading.Lock()
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/130.0.0.0 Safari/537.36")


IGNORED_PATH = config.DATA / "ignored_ids.json"  # 用户删掉的条目（uid）：只有「代刷」会跳过它们；同步收藏、手动添加照样能加回来


def ignored_ids():
    try:
        return set(json.loads(IGNORED_PATH.read_text(encoding="utf-8")))
    except Exception:
        return set()


def ignore_ids(ids):
    """记下删掉的条目（存 uid，比如 dy:123 / xhs:abc；以前存的是裸作品 ID，也照样认）。"""
    s = ignored_ids() | {str(x) for x in ids if x}
    IGNORED_PATH.write_text(json.dumps(sorted(s)), encoding="utf-8")


def is_ignored(ign, platform, aweme_id):
    return db.make_uid(platform, aweme_id) in ign or str(aweme_id) in ign


PURGED_PATH = config.DATA / "purged.json"  # 回收站里「彻底删除」的：记录删掉了，元信息留在这里备查


def remember_purged(it):
    try:
        lst = json.loads(PURGED_PATH.read_text(encoding="utf-8"))
    except Exception:
        lst = []
    lst.append({k: it.get(k) for k in ("id", "uid", "aweme_id", "platform", "title", "author", "url", "local_path",
                                        "category", "created_at", "fav_at")} | {"purged_at": time.time()})
    PURGED_PATH.write_text(json.dumps(lst, ensure_ascii=False, indent=1), encoding="utf-8")


FORCE_PATH = config.DATA / "force_extract.json"


PRIORITY_PATH = config.DATA / "priority.json"  # 代刷来的条目：插队先处理


def _load_force(path=None):
    try:
        return {int(x) for x in json.loads((path or FORCE_PATH).read_text(encoding="utf-8"))}
    except Exception:
        return set()


def _save_force(ids, path=None):
    try:
        (path or FORCE_PATH).write_text(json.dumps(sorted(ids)), encoding="utf-8")
    except Exception:
        pass


def _platform_of_url(url):
    return "xhs" if any(h in (url or "") for h in xhs.HOSTS) else "douyin"


def _job_dir(job):
    base = config.download_dir(job.get("platform") or "douyin")
    return base / job["subdir"] if job.get("subdir") else base


def _http_get(url, timeout=60, headers=None):
    ref = "https://www.xiaohongshu.com/" if "xhscdn" in url or "xiaohongshu" in url else "https://www.douyin.com/"
    req = urllib.request.Request(url, headers={"Referer": ref, "User-Agent": _UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)


def _http_file(urls, dest: Path):
    """普通 HTTP 下载视频，边下边写到 .part，完成后再改名。依次试每个地址。
    CDN 有时下到一半就断开（只给整数 MB），按 Range 续传直到和 Content-Length 一样长。"""
    last = "没有可用的视频地址"
    part = dest.with_name(dest.name + ".part")
    for u in urls:
        try:
            n, total = 0, None
            with open(part, "wb") as f:
                for _ in range(30):
                    with _http_get(u, headers={"Range": f"bytes={n}-"} if n else None) as r:
                        if total is None:
                            total = int(r.headers.get("Content-Length") or 0) or None
                        elif r.status != 206:  # 服务器不支持续传：从头再来
                            f.seek(0)
                            f.truncate()
                            n = 0
                        while True:
                            try:
                                b = r.read(512 * 1024)
                            except Exception:
                                break
                            if not b:
                                break
                            f.write(b)
                            n += len(b)
                    if total is None or n >= total:
                        break
            if total and n < total:
                last = f"下载不完整（{n}/{total}）"
                continue
            if n < 10 * 1024:  # CDN 出错时返回几 KB 的错误页
                last = "下载到的文件太小，可能是错误页"
                continue
            part.replace(dest)
            return
        except Exception as e:
            last = str(e)[:120]
        finally:
            part.unlink(missing_ok=True)
    raise douyin.DouyinError(f"视频下载失败：{last}")


def _http_images(images, folder: Path):
    saved = 0
    for n, cands in enumerate(images, 1):
        for u in cands:
            try:
                with _http_get(u, timeout=30) as r:
                    body = r.read()
                    ctype = (r.headers.get("content-type") or "").lower()
                if len(body) < 2048:
                    continue
                ext = ".webp" if "webp" in ctype else ".png" if "png" in ctype else ".heic" if "heic" in ctype else ".jpg"
                (folder / f"{n:02d}{ext}").write_bytes(body)
                saved += 1
                break
            except Exception:
                continue
    if not saved:
        raise douyin.DouyinError("图文的图片都下载失败了")


def _load_downloaded():
    try:
        return json.loads(DOWNLOADED_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def downloaded_path(aweme_id, in_dir=None):
    """这个作品之前下载过、而且文件还在，就返回保存位置；否则返回 None（文件被删了可以重新下）。

    in_dir：只看下载到这个文件夹里的（同一条收藏可能在几个分类里各存一份）。
    """
    if not aweme_id:
        return None
    with _dl_lock:
        v = _load_downloaded().get(str(aweme_id))
    paths = [v] if isinstance(v, str) else list(v or [])
    for p in paths:
        pp = Path(p)
        if pp.exists() and (in_dir is None or pp.parent.resolve() == Path(in_dir).resolve()):
            return p
    # 本地重新归类过的（用户把文件挪到了别的分类文件夹）：算已下载，同步收藏夹时不再复制回原分类
    moved = _load_relocated().get(str(aweme_id))
    if moved and Path(moved).exists():
        return moved
    return None


RELOCATED_PATH = config.DATA / "relocated.json"  # 本地重新归类：作品 ID → 挪到的新位置


def _load_relocated():
    try:
        return json.loads(RELOCATED_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def mark_downloaded(aweme_id, path, replace=None):
    with _dl_lock:
        data = _load_downloaded()
        v = data.get(str(aweme_id))
        paths = [v] if isinstance(v, str) else list(v or [])
        if replace:
            paths = [p for p in paths if p != str(replace)]
        if str(path) not in paths:
            paths.append(str(path))
        data[str(aweme_id)] = paths
        tmp = DOWNLOADED_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
        tmp.replace(DOWNLOADED_PATH)


def _safe_name(s, limit=80):
    """文件名去掉 Windows 不允许的字符、话题标签和换行，太长就截断。"""
    import re
    s = re.sub(r"#\S*", "", s)
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return (s[:limit].rstrip(" .") or "抖音视频")


def _unique(folder: Path, name: str, ext: str = "") -> Path:
    """重名时加 (2)、(3)…；ext 单独传，免得标题里的点被当成扩展名。"""
    p = folder / f"{name}{ext}"
    for i in range(2, 1000):
        if not p.exists():
            return p
        p = folder / f"{name} ({i}){ext}"
    return p


def import_folder(folder, limit=None, include_notes=True):
    """把一个本地文件夹（通常是「仅下载」存下来的收藏夹分类）加进学习台，不复制、不下载、不改原文件。

    顺序：按文件写入时间从早到晚，也就是当初同步下载的顺序 = 收藏夹从新到旧，所以 limit=200 就是最近收藏的 200 条。
    视频文件 → 一条视频；文件夹（图片 + 正文.txt）→ 一条图文（include_notes=False 时跳过）。
    能在下载记录里对上作品 ID 的会带上 ID 和原链接，已经在学习台里的同一作品不会重复添加。
    返回 {"ids": [...按顺序的条目 id], "added": 新增条数, "existing": 已存在条数}。
    """
    import os
    folder = Path(folder)
    if not str(folder).strip() or not folder.is_absolute():
        raise ValueError("请填完整的文件夹路径，例如 D:\\douyin\\厉害")
    if not folder.is_dir():
        raise ValueError(f"文件夹不存在：{folder}")
    with _dl_lock:
        rec = _load_downloaded()
    by_path = {}
    for aid, v in rec.items():
        for p in ([v] if isinstance(v, str) else (v or [])):
            by_path[os.path.normcase(str(p))] = aid
    def is_note_dir(d):
        # 图文笔记文件夹：有图片或 正文.txt，自己没有视频、没有子文件夹（分类文件夹、「超过500M」这类不算）
        if d.name.startswith(("_", ".")):
            return False
        fs = list(d.iterdir())
        return ((d / "正文.txt").exists() or any(f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") for f in fs)) \
            and not any(f.is_dir() or f.suffix.lower() in MEDIA_EXT for f in fs)

    entries = [p for p in folder.iterdir()
               if (p.is_file() and p.suffix.lower() in MEDIA_EXT) or (include_notes and p.is_dir() and is_note_dir(p))]
    entries.sort(key=lambda p: (p.stat().st_mtime, p.name))
    if limit:
        entries = entries[:int(limit)]
    ids, added = [], 0
    for p in entries:
        aid = by_path.get(os.path.normcase(str(p)))
        is_note = p.is_dir()
        name = p.stem if not is_note else p.name
        author, _, title = name.partition(" - ")
        if not title:
            author, title = "", name
        if is_note and (p / "正文.txt").exists():
            body = (p / "正文.txt").read_text(encoding="utf-8", errors="ignore").strip()
            if body and title.startswith("抖音图文"):
                title = body.splitlines()[0][:80]
        url = f"https://www.douyin.com/{'note' if is_note else 'video'}/{aid}" if aid else None
        item_id, new = db.add("local", aweme_id=aid, url=url, local_path=str(p), title=title, author=author)
        cur = db.get(item_id)
        if new:
            added += 1
        elif not cur.get("transcript") and cur["status"] in ("queued", "failed"):
            # 学习台里已经有这条（比如同步收藏夹时加的）但还没转文字：改成用本地文件，免得再去下载
            db.update(item_id, source="local", local_path=str(p), status="queued", attempts=0, reason=None)
        if is_note:
            db.update(item_id, media_type="note")
        ids.append(item_id)
    return {"ids": ids, "added": added, "existing": len(ids) - added}


def scan_inbox():
    """把 data/inbox 文件夹里的音视频加入队列。返回新增条数。"""
    n = 0
    for p in sorted(config.INBOX_DIR.rglob("*")):
        if p.is_file() and p.suffix.lower() in MEDIA_EXT:
            _, new = db.add("local", local_path=str(p), title=p.stem)
            n += int(new)
    return n
