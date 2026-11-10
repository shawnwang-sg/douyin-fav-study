"""桌面快捷方式用的启动器：学习台没开就启动它（黑窗口最小化到任务栏），已经开着就直接打开学习库网页。

用 pythonw 运行（不弹黑窗口）：  .venv\\Scripts\\pythonw.exe launch.pyw
要退出学习台：关掉任务栏里那个最小化的「收藏学习台」黑窗口。
"""
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
URL = BASE + "/reader"  # 打开就是学习库


def alive():
    """8765 上是不是学习台在跑。"""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 本机地址不走代理
        with opener.open(BASE + "/api/state", timeout=3) as r:
            return b'"version"' in r.read()
    except Exception:
        return False


def start():
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        py = Path(sys.executable).with_name("python.exe")
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 7  # SW_SHOWMINNOACTIVE：最小化，不抢焦点
    # cmd /c "title … && "python.exe" -m app"：给黑窗口起个认得出的名字
    cmd = f'cmd /c "title 收藏学习台 - 关掉这个窗口就退出 && "{py}" -X utf8 -m app --no-browser"'
    subprocess.Popen(cmd, cwd=str(ROOT), startupinfo=si, creationflags=subprocess.CREATE_NEW_CONSOLE)


def warn(msg):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, "收藏学习台", 0x30)
    except Exception:
        pass


if __name__ == "__main__":
    if not alive():
        start()
        for _ in range(120):  # 第一次启动要载入组件，最多等 60 秒
            time.sleep(0.5)
            if alive():
                break
        else:
            warn("学习台没能启动起来。\n请双击程序目录里的 start_windows.bat，看黑窗口里的报错。")
            sys.exit(1)
    webbrowser.open(URL)
