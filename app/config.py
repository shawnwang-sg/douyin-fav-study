"""设置读写与路径。所有数据都放在程序目录下的 data/ 里，删掉程序文件夹就全部清干净。"""
import json
import os
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 数据目录：默认是程序目录下的 data/；设环境变量 FAVSTUDY_DATA 可以换到别处（比如跑一份演示数据）
DATA = Path(os.environ.get("FAVSTUDY_DATA") or (ROOT / "data")).expanduser()
DB_PATH = DATA / "library.db"
SETTINGS_PATH = DATA / "settings.json"
MEDIA_DIR = DATA / "media"          # 临时视频，转录完即删
INBOX_DIR = DATA / "inbox"          # 手动放进来的本地视频
MODELS_DIR = DATA / "models"        # Whisper 模型缓存
BROWSER_DIR = DATA / "browser"      # 抖音登录态（浏览器配置目录）
XHS_BROWSER_DIR = DATA / "browser_xhs"  # 小红书登录态（和抖音分开，互不影响）
DEFAULT_LIBRARY = DATA / "知识库"    # Markdown 笔记（可当 Obsidian 库打开）
VIDEO_LIBRARY = ROOT / "视频库"     # 本地视频库：下载的视频、图文都放这里，按平台、收藏夹分类分文件夹
DEFAULT_DOWNLOADS = VIDEO_LIBRARY / "抖音"
DEFAULT_XHS_DOWNLOADS = VIDEO_LIBRARY / "小红书"
TOOLS_DIR = ROOT / "tools"          # 外部工具（XHS-Downloader 等）

PLATFORMS = {"douyin": "抖音", "xhs": "小红书"}

for d in (DATA, MEDIA_DIR, INBOX_DIR, MODELS_DIR, BROWSER_DIR, XHS_BROWSER_DIR):
    d.mkdir(parents=True, exist_ok=True)

# AI 服务商预设。kind=openai 走 OpenAI 兼容的 /chat/completions；kind=anthropic 走 Claude 原生接口。
# 模型名各家会更新，设置页里可以随时改。
PROVIDERS = {
    # 本机 Claude Code（claude -p）：走你自己的 Claude 订阅额度，不需要 API Key
    "claude_cli": {"name": "Claude Code（本机订阅，免 Key）", "base_url": "local://claude-code", "model": "sonnet",
                   "kind": "claude_cli", "no_key": True, "key_url": "", "models": ["sonnet", "opus", "haiku"]},
    # 本机 Codex CLI（codex exec）：走你自己的 ChatGPT 订阅额度，不需要 API Key
    "codex_cli": {"name": "Codex（本机 ChatGPT 订阅，免 Key）", "base_url": "local://codex", "model": "default",
                  "kind": "codex_cli", "no_key": True, "key_url": "", "models": ["default", "gpt-5.5", "gpt-5.5-mini"]},
    "anthropic": {"name": "Claude（Anthropic）", "base_url": "https://api.anthropic.com", "model": "claude-sonnet-5-5",
                  "key_url": "https://console.anthropic.com/settings/keys", "kind": "anthropic",
                  "models": ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"]},
    "deepseek": {"name": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "deepseek-chat",
                 "key_url": "https://platform.deepseek.com/api_keys", "models": ["deepseek-chat", "deepseek-reasoner"]},
    "kimi": {"name": "Kimi（月之暗面）", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k",
             "key_url": "https://platform.moonshot.cn/console/api-keys"},
    "qwen": {"name": "通义千问", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus",
             "key_url": "https://bailian.console.aliyun.com/"},
    "zhipu": {"name": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4.7-flash",
              "models": ["glm-4.7-flash", "glm-4.5-flash"],
              "key_url": "https://open.bigmodel.cn/usercenter/apikeys"},
    "siliconflow": {"name": "硅基流动", "base_url": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V3",
                    "key_url": "https://cloud.siliconflow.cn/account/ak"},
    "openai": {"name": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini",
               "key_url": "https://platform.openai.com/api-keys"},
    "custom": {"name": "自定义（任意 OpenAI 兼容接口）", "base_url": "", "model": "", "key_url": ""},
}

# 免费方案：每个用户用自己账号的免费额度（不把谁的 Key 打包进软件）。
# models 是按优先顺序尝试的免费模型；服务商会更新模型，一键配置时会先读服务商的模型列表再挑。
FREE_PLANS = {
    "zhipu": {
        "name": "智谱 GLM", "tag": "国内推荐", "provider": "zhipu",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4.7-flash", "glm-4.5-flash", "glm-4-flash-250414", "glm-4-flash"],
        "pick": ["glm", "flash"], "avoid": ["flashx", "air", "plus", "vision", "4v", "voice", "image", "video"],
        "signup": "https://open.bigmodel.cn/", "key_url": "https://open.bigmodel.cn/usercenter/apikeys",
        "desc": "国内直连，Flash 系列模型免费调用，中文好。手机号注册即可。",
    },
    "siliconflow": {
        "name": "硅基流动", "tag": "国内", "provider": "siliconflow",
        "base_url": "https://api.siliconflow.cn/v1",
        "models": ["Qwen/Qwen3-8B", "THUDM/GLM-4-9B-0414", "THUDM/glm-4-9b-chat", "Qwen/Qwen2.5-7B-Instruct"],
        "pick": [], "avoid": [],
        "signup": "https://cloud.siliconflow.cn/", "key_url": "https://cloud.siliconflow.cn/account/ak",
        "desc": "国内直连，有一批免费的小模型，注册还送体验额度。",
    },
    "gemini": {
        "name": "Google Gemini", "tag": "海外", "provider": "custom",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "models": ["gemini-3.8-flash", "gemini-2.5-flash"],
        "pick": ["gemini", "flash"], "avoid": ["image", "tts", "live", "audio", "embedding", "thinking-exp", "lite", "preview-tts"],
        "signup": "https://aistudio.google.com/", "key_url": "https://aistudio.google.com/apikey",
        "desc": "免费额度多、能力强，但国内无法直接访问；免费版的内容可能被 Google 用来改进模型。",
    },
}

DEFAULT_CATEGORIES = [
    "AI 工具推荐", "提示词技巧", "AI 编程", "AI 绘画与视频", "AI 办公效率",
    "大模型与行业资讯", "AI 副业与变现", "AI 学习方法", "其他",
]

DEFAULT_DEEP_PROMPT = (
    "这是一段视频的文字稿，我还没读。请先给我 5 句话的核心结论，再按章节列大纲，标出关键概念和术语解释；"
    "然后不要直接给答案，用提问的方式检查我是否真正理解；最后出 10 道测验题，"
    "并把要点做成「问题<Tab>答案」格式的 Anki 卡片。"
)

DEFAULTS = {
    "provider": "deepseek",
    "base_url": PROVIDERS["deepseek"]["base_url"],
    "model": PROVIDERS["deepseek"]["model"],
    "api_key": "",
    "focus_topic": "AI / 人工智能相关的干货：AI 工具、提示词、AI 编程、AI 绘画和视频、大模型、用 AI 提升效率或赚钱的方法",
    "only_relevant": True,              # 只保留和关注主题相关的视频
    "categories": DEFAULT_CATEGORIES,
    "asr_engine": "auto",               # auto / mlx / faster —— auto：苹果芯片 Mac 用 mlx，其他用 faster-whisper
    "mlx_model": "mlx-community/whisper-large-v3-turbo",
    "whisper_model": "small",           # faster-whisper 用：tiny / base / small / medium
    "asr_workers": 4,                   # 同时转几条（本地文件并行转写；在线视频仍一条一条来）
    "old_yuedu_libs": [],               # 以前用 video-yuedu 整理过的本地库（导入旧阅读稿用），如 ["D:/douyin"]
    "asr_lane_roots": [],               # 按库分路：如 ["D:/douyin", "D:/xiaohongshu"]，第 k 路优先转第 k % N 个库，没有了再帮别的库
    "use_subtitles": True,              # 视频自带字幕时直接用字幕，跳过下载和转写
    "use_hf_mirror": False,             # 在中国大陆时勾选：用 hf-mirror.com 下载模型
    "max_minutes": 15,                  # 超长视频只转录前 N 分钟
    "library_dir": "",                  # 留空 = 程序目录下的 data/知识库
    "keep_video": True,                 # 处理过的视频留在本地视频库（学习库里随时能看原视频）
    "skip_long": True,                  # 超过 max_minutes 的视频整条跳过（不只截前 N 分钟）
    "auto_extract": True,              # False = 只转文字，转好后停在「待提炼」，手动点「提炼」
    "import_dir": "",                   # 上次「导入本地文件夹」用的文件夹
    "download_dir": "",                 # 抖音视频库（下载位置），空 = 程序目录\视频库\抖音
    "xhs_download_dir": "",             # 小红书视频库，空 = 程序目录\视频库\小红书
    "xhs_downloader_dir": "",           # XHS-Downloader 的位置，空 = 程序目录\tools\XHS-Downloader（没有就用 用户目录\XHS-Downloader）
    "xhs_folders": [],                  # 小红书收藏专辑缓存
    "xhs_folders_at": 0,
    "sync_platform": "douyin",          # 上次同步选的平台：douyin / xhs
    "xhs_delay": [4, 9],                # 小红书每读一条详情之间随机歇几秒（防风控）
    "xhs_daily_cap": 400,               # 小红书每天最多读多少条详情，超过就停到第二天（防封号）
    "web_parse_fallback": True,         # XHS-Downloader 读不到 / 限流时，用网页解析站（kukutool，免登录、不付费）兜底
    "refine_mode": "yuedu",             # yuedu = 保留原话润色排版（改动 ≤15%）；notes = 旧的结构化学习笔记
    "max_change_pct": 15,               # 润色改动上限（%）
    "weekly_days": 7,                   # 「整理这一周」往回看几天的收藏
    "last_weekly_sync": 0,
    "folders": [],                      # 收藏夹分类缓存 [{"id", "name", "count"}]
    "folders_at": 0,
    "sync_kind": "collection",          # 上次同步选的列表：collection / like / folder:<id>
    "sync_download_only": False,
    "browser": "auto",                  # auto / msedge / chrome / chromium
    "sync_limit": 50,                   # 每次同步收藏夹最多读多少条
    # ---- 精读（「有人带着读」）：可以和日常提炼用不同的模型
    "deep_same": True,                  # True = 和上面的 AI 提炼用同一个模型
    "deep_provider": "anthropic",
    "deep_base_url": PROVIDERS["anthropic"]["base_url"],
    "deep_model": PROVIDERS["anthropic"]["model"],
    "deep_api_key": "",
    "deep_prompt": DEFAULT_DEEP_PROMPT,
    "deep_chunk_chars": 12000,          # 文稿超过这个字数就分段总结再合并
    "free_plan": "",                    # 正在用的免费方案（空 = 没用）
    "welcome_done": False,              # 新手引导已经关掉
    "onboard_topic": "",                # 新手引导里选的主题（空 = 还没选）
    "check_update": True,               # 启动时去 GitHub 看看有没有新版本（只问版本号，不上传任何东西）
    "tokens_used": 0,
    "tokens_weekly": {},                # 每周消耗：{"本周一的日期": token 数}，周报用，只留最近 12 周
    "weekly_seen": "",                  # 看过的最近一期周报（那周周一的日期）
    "timing": {},                       # 各步骤实际耗时（用来估算剩余时间）
}

_lock = threading.Lock()


def load():
    with _lock:
        s = dict(DEFAULTS)
        if SETTINGS_PATH.exists():
            try:
                s.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
            except Exception:
                pass
        return s


def save(patch: dict):
    with _lock:
        s = dict(DEFAULTS)
        if SETTINGS_PATH.exists():
            try:
                s.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
            except Exception:
                pass
        for k, v in patch.items():
            if k in DEFAULTS:
                s[k] = v
        fp = FREE_PLANS.get(s.get("free_plan") or "")
        if fp and (s.get("base_url") or "").rstrip("/") != fp["base_url"]:
            s["free_plan"] = ""  # 手动换成了别的服务，就不算免费方案了
        tmp = SETTINGS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, SETTINGS_PATH)
        return s


def week_key(t=None) -> str:
    """t 所在那一周的周一（本地时间），例如 "2026-10-26"。"""
    import datetime
    d = datetime.date.fromtimestamp(t if t is not None else time.time())
    return (d - datetime.timedelta(days=d.weekday())).isoformat()


def add_tokens(n: int):
    if n:
        s = load()
        weekly = dict(s.get("tokens_weekly") or {})
        wk = week_key()
        weekly[wk] = int(weekly.get(wk, 0)) + int(n)
        weekly = dict(sorted(weekly.items())[-12:])
        save({"tokens_used": int(s.get("tokens_used", 0)) + int(n), "tokens_weekly": weekly})


def public(s: dict) -> dict:
    """给网页看的设置：Key 只露出末尾 4 位。"""
    out = dict(s)
    for field in ("api_key", "deep_api_key"):
        k = out.pop(field, "") or ""
        out[field + "_set"] = bool(k) or _uses_cli(s, "deep" if field == "deep_api_key" else "main")
        out[field + "_hint"] = ("••••" + k[-4:]) if len(k) >= 4 else ""
    out.pop("timing", None)
    out["default_deep_prompt"] = DEFAULT_DEEP_PROMPT
    out["default_focus_topic"] = DEFAULTS["focus_topic"]
    out["default_categories"] = DEFAULT_CATEGORIES
    return out


def provider_kind(provider, base_url=""):
    p = PROVIDERS.get(provider) or {}
    if p.get("kind"):
        return p["kind"]
    return "anthropic" if "anthropic.com" in (base_url or "") else "openai"


def llm_cfg(s, purpose="main"):
    """返回调用大模型需要的配置。purpose = main（日常提炼）或 deep（精读）。"""
    if purpose == "deep" and not s.get("deep_same", True):
        cfg = {"provider": s.get("deep_provider"), "base_url": s.get("deep_base_url"),
               "model": s.get("deep_model"), "api_key": s.get("deep_api_key")}
    else:
        cfg = {"provider": s.get("provider"), "base_url": s.get("base_url"),
               "model": s.get("model"), "api_key": s.get("api_key")}
    cfg["kind"] = provider_kind(cfg["provider"], cfg["base_url"])
    if cfg["kind"] in ("claude_cli", "codex_cli") and not (cfg.get("api_key") or "").strip():
        cfg["api_key"] = "local-subscription"  # 占位：本机订阅不需要 Key，让各处的「填了 Key 没有」检查通过
    return cfg


def _uses_cli(s, purpose="main"):
    if purpose == "deep" and not s.get("deep_same", True):
        return provider_kind(s.get("deep_provider"), s.get("deep_base_url")) in ("claude_cli", "codex_cli")
    return provider_kind(s.get("provider"), s.get("base_url")) in ("claude_cli", "codex_cli")


def download_dir(platform="douyin") -> Path:
    s = load()
    if platform == "xhs":
        p = Path(s.get("xhs_download_dir") or DEFAULT_XHS_DOWNLOADS).expanduser()
    else:
        p = Path(s.get("download_dir") or DEFAULT_DOWNLOADS).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def platform_of(aweme_id="", local_path=""):
    """按作品 ID / 本地路径判断是哪个平台：小红书笔记 ID 是 24 位十六进制，抖音是纯数字。"""
    import re
    if aweme_id and re.fullmatch(r"[0-9a-f]{24}", str(aweme_id)):
        return "xhs"
    if aweme_id and str(aweme_id).isdigit():
        return "douyin"
    if local_path:
        lp = os.path.normcase(str(local_path))
        try:
            xr = os.path.normcase(str(download_dir("xhs")))
            if lp.startswith(xr.rstrip("/" + os.sep) + os.sep):
                return "xhs"
        except Exception:
            pass
        if "xiaohongshu" in lp or "小红书" in lp:
            return "xhs"
    return "douyin"


def library_dir() -> Path:
    p = Path(load().get("library_dir") or DEFAULT_LIBRARY).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p
