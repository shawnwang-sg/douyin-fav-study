"""在 XHS-Downloader 自己的虚拟环境里运行的小桥：从标准输入读链接，输出笔记数据。

用法（由 app/xhs.py 启动，不需要手动运行）：
    <XHS-Downloader>/.venv/Scripts/python.exe xhs_bridge.py <XHS-Downloader 目录> <临时目录>
每行输入一个 JSON：{"url": "..."}；每条结果输出一行 "@@RESULT <json>"（其它行是 XHS-Downloader 自己的日志）。
Cookie 从环境变量 XHS_COOKIE 读，请求间隔从 XHS_DELAY 读（秒，默认 2）。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, sys.argv[1])
from source import XHS  # noqa: E402
from source.application.video import Video  # noqa: E402

if os.environ.get("XHS_QUALITY", "low") == "low":
    # 用户要最低画质（省空间）。也只挑 h264：原画（originVideoKey）常是没有音轨的 HEVC，下下来没声音。
    _orig = Video.deal_video_link.__func__

    def _lowest(cls, data, preference="resolution"):
        for key in ("h264", "h265", "av1", "h266"):
            items = data.safe_extract(f"video.media.stream.{key}", []) or []
            items = [i for i in items if getattr(i, "masterUrl", None) or getattr(i, "backupUrls", None)]
            if items:
                items.sort(key=lambda x: (getattr(x, "size", 0) or 0, getattr(x, "height", 0) or 0))
                b = items[0]
                return [b.masterUrl] if getattr(b, "masterUrl", None) else [b.backupUrls[0]]
        return _orig(cls, data, preference)

    Video.deal_video_link = classmethod(_lowest)


def emit(obj):
    sys.stdout.write("@@RESULT " + json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


async def main():
    loop = asyncio.get_running_loop()
    async with XHS(work_path=sys.argv[2], folder_name="bridge", download_record=False, record_data=False,
                   image_format="JPEG", cookie=os.environ.get("XHS_COOKIE", ""),
                   avg_delay=float(os.environ.get("XHS_DELAY", "2")), timeout=15, max_retry=2,
                   language="zh_CN") as x:
        run = x.explore.run

        def run_with_duration(data):  # XHS-Downloader 不输出视频时长，这里补上（秒）
            r = run(data)
            if r:
                for chain in ("video.capa.duration", "video.media.video.duration"):
                    v = data.safe_extract(chain, 0)
                    if v:
                        r["视频时长"] = float(v) / (1000 if float(v) > 100000 else 1)
                        break
            return r

        x.explore.run = run_with_duration
        emit({"ready": True})
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            try:
                req = json.loads(line)
                res = await x.extract(req["url"], False)
                emit({"ok": True, "data": (res or [{}])[0] or {}})
            except Exception as e:  # 单条失败不影响后面的
                emit({"ok": False, "error": f"{type(e).__name__}: {e}"[:300]})


if __name__ == "__main__":
    asyncio.run(main())
