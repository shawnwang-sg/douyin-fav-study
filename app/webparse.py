"""网页解析站兜底：XHS-Downloader 读不到 / 被限流时，用免登录的解析网站拿视频地址。

- 不登录你的小红书账号，不付费（遇到「开通会员 / 次数用完」直接放弃，不点任何付费按钮）。
- 拿到的是最低画质那一路（页面 <video> 的地址，一般是 _258.mp4），省空间。
- 一次只解析一条，每条之间随机歇几秒；自己一个线程、一个独立的浏览器（无痕、无登录信息）。
"""
import queue
import random
import re
import threading
import time

SITES = {
    "kukutool": "https://dy.kukutool.com/xiaohongshu",
}

_PAY_WORDS = ("开通会员", "次数已用完", "试用次数", "充值", "购买")

_PARSE_JS = r"""async (url) => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const prev = new Set([...document.querySelectorAll('video')].map(v => v.src));
  const btn = t => [...document.querySelectorAll('button')].find(b => b.innerText.trim() === t);
  const clr = btn('清空'); if (clr) { clr.click(); await sleep(300); }
  const inp = document.querySelector('main input, main textarea, input, textarea');
  if (!inp) return {err: '页面没有输入框'};
  const set = Object.getOwnPropertyDescriptor(inp.tagName === 'INPUT' ? HTMLInputElement.prototype
                                              : HTMLTextAreaElement.prototype, 'value').set;
  set.call(inp, url); inp.dispatchEvent(new Event('input', {bubbles: true})); await sleep(300);
  const go = btn('开始解析'); if (!go) return {err: '没找到「开始解析」按钮'};
  go.click();
  for (let k = 0; k < 30; k++) {
    await sleep(1000);
    const vs = [...document.querySelectorAll('video')].map(v => v.src).filter(s => s && !prev.has(s));
    if (vs.length) return {video: vs[0]};
    const imgs = [...document.querySelectorAll('main img')].map(i => i.src)
      .filter(s => /sns-img|xhscdn/.test(s));
    const text = (document.querySelector('main') || document.body).innerText + ' ' +
      [...document.querySelectorAll('[role=dialog],.modal,.el-dialog,.ant-modal')].map(d => d.innerText).join(' ');
    if (k > 3 && imgs.length && /图文|图片/.test(text)) return {images: [...new Set(imgs)]};
    if (k > 3 && /解析失败|失败|错误/.test(text)) return {err: text.slice(0, 120)};
    if (/开通会员|次数已用完|试用次数|充值/.test(text)) return {err: 'PAYWALL'};
  }
  return {err: 'timeout'};
}"""


class WebParseError(Exception):
    pass


class WebParser:
    """单独线程里跑一个无登录的浏览器，按顺序解析链接。"""

    def __init__(self, site="kukutool", log=print):
        self.url = SITES[site]
        self.log = log
        self.q = queue.Queue()
        self.thread = None
        self.dead_until = 0.0  # 网站要付费 / 连不上时，先停用一阵

    def _loop(self):
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = page = None
        last = 0.0
        try:
            while True:
                url, box, done = self.q.get()
                try:
                    if browser is None:
                        browser = pw.chromium.launch(headless=True, channel="msedge") if _has_edge() else \
                            pw.chromium.launch(headless=True)
                        page = browser.new_context(locale="zh-CN").new_page()
                        page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_timeout(2500)
                    gap = time.time() - last
                    if gap < 3:
                        time.sleep(random.uniform(2.5, 5.0) - gap if gap < 2.5 else 0.5)
                    for sel in ("text=我知道了", "text=关闭", "text=跳过"):  # 弹窗：只点关闭类按钮
                        try:
                            page.locator(sel).first.click(timeout=500)
                        except Exception:
                            pass
                    box["r"] = page.evaluate(_PARSE_JS, url)
                    last = time.time()
                except Exception as e:
                    box["r"] = {"err": f"{type(e).__name__}: {str(e)[:120]}"}
                    try:
                        page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
                    except Exception:
                        pass
                finally:
                    done.set()
        finally:
            try:
                browser and browser.close()
            finally:
                pw.stop()

    def parse(self, url, timeout=90):
        """返回 {"video": 地址} 或 {"images": [...]}；失败抛 WebParseError。"""
        if time.time() < self.dead_until:
            raise WebParseError("网页解析站暂时停用")
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._loop, daemon=True, name="webparse")
            self.thread.start()
        box, done = {}, threading.Event()
        self.q.put((url, box, done))
        if not done.wait(timeout):
            raise WebParseError("网页解析超时")
        r = box.get("r") or {}
        if r.get("err") == "PAYWALL":
            self.dead_until = time.time() + 6 * 3600
            raise WebParseError("解析站要求付费 / 免费次数用完，已停用 6 小时（不会付钱）")
        if r.get("err"):
            raise WebParseError("网页解析失败：" + r["err"][:80])
        return r


def _has_edge():
    import shutil
    import os
    return bool(shutil.which("msedge")) or os.path.exists(
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


_PARSER = None


def parser(log=print):
    global _PARSER
    if _PARSER is None:
        _PARSER = WebParser(log=log)
    return _PARSER


def strip_link(url):
    """解析站只认干净的 explore 链接：去掉 xsec_source 等多余参数，保留 xsec_token。"""
    m = re.search(r"/explore/([0-9a-f]{24})", url or "")
    if not m:
        return url
    tok = re.search(r"xsec_token=([^&]+)", url)
    return f"https://www.xiaohongshu.com/explore/{m.group(1)}" + (f"?xsec_token={tok.group(1)}" if tok else "")
