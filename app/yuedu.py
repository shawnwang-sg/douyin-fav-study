"""第三步「提炼」（新版）：保留原话的润色排版，规则同 video-yuedu skill。

只改转写错字、删口水词、补标点断句，视频改动 ≤15%（设置里 max_change_pct）；
排成好读的文章（分段、## 小标题、编号、引用、1～3 处加粗、一句原文金句），
并归到统一的分类：tab（学习库顶部）→ 大类 → 小主题（学习库左侧目录）。

结果存在 items.note_json：{"mode": "yuedu", "verdict", "tab", "category", "topic", "theme", "gold", "body",
"change_pct", "unsure", "tags", ...}；items.category = tab。
"""
import collections
import difflib
import json
import os
import re
import unicodedata
from pathlib import Path

from . import config, llm

HERE = Path(__file__).resolve().parent
FORMAT_MD = HERE / "yuedu_format.md"
TAXONOMY_PATH = config.DATA / "taxonomy.json"

# 已有的阅读库（video-yuedu skill 建的），合并成一个学习库：在设置 old_yuedu_libs 里填，比如 ["D:/douyin"]
def old_libs():
    return [Path(p) for p in (config.load().get("old_yuedu_libs") or []) if p]

DEFAULT_TABS = [
    {"name": "厉害", "desc": "人生信条、认知、处世、成长；职场、理财、名人访谈"},
    {"name": "爱好", "desc": "兴趣技能教学：网球、游泳、武术、体态拉伸、发声、书法、外语、AI 与编程"},
    {"name": "厉害文字", "desc": "金句、人生信条的图文"},
    {"name": "身体健康", "desc": "健康养生、睡眠、饮食营养、运动康复、面部"},
    {"name": "美食", "desc": "做菜吃喝、菜谱、探店"},
]
OTHER = "其他"
LOW_TYPES = ("画面为主", "断续口播", "纯音乐歌词", "残缺", "转写太乱（待重转）", "其他")


# ---------------------------------------------------------------- 分类表

def taxonomy():
    """{"tabs": [{"name", "desc"}], "categories": {tab: {大类: [小主题]}}}。第一次用时从两个旧库合并。"""
    if TAXONOMY_PATH.exists():
        try:
            return json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    cats = {}
    for f in (config.DATA / "categories_douyin.json", config.DATA / "categories_xhs.json",
              *(lib / "_yuedu" / "categories.json" for lib in old_libs())):
        if not f.exists():
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for tab, v in d.items():
            if isinstance(v, str):  # 「厉害文字」: "厉害" —— 共用一套大类
                v = d.get(v) or {}
            dst = cats.setdefault(tab, {})
            for big, topics in v.items():
                have = dst.setdefault(big, [])
                have += [t for t in topics if t not in have]
    t = {"tabs": DEFAULT_TABS, "categories": cats}
    save_taxonomy(t)
    return t


def save_taxonomy(t):
    TAXONOMY_PATH.write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")


def _cat_key(c):
    nums = "一二三四五六七八九十"
    m = re.match(r"([一二三四五六七八九十]+)、", c or "")
    if not m:
        return 99
    s = m.group(1)
    if s == "十":
        return 10
    if s.startswith("十"):
        return 10 + nums.index(s[1]) + 1
    return nums.index(s[0]) + 1


def learn_topic(tab, big, topic):
    """新出现的小主题记进分类表，下次 AI 优先复用。"""
    if not (tab and big and topic):
        return
    t = taxonomy()
    lst = t["categories"].setdefault(tab, {}).setdefault(big, [])
    if topic not in lst:
        lst.append(topic)
        save_taxonomy(t)


# ---------------------------------------------------------------- 改动比例（和 skill 的 build_yuedu.change_pct 一致）

def _strip(s):
    return "".join(ch for ch in s if not ch.isspace() and unicodedata.category(ch)[0] not in "PSZ")


def _norm(s):
    s = s.replace("[?]", "").replace("（原视频到此结束）", "")
    s = re.sub(r"(?ms)^## (食材|用料|配料|调料)\s*$.*?(?=^## |\Z)", "", s)
    s = re.sub(r"(?m)^## (做法|步骤|准备)\s*$", "", s)
    s = re.sub(r"(?m)^## ", "", s)
    s = re.sub(r"(?m)^\d+\. ", "", s)
    s = re.sub(r"(?m)^> [^：:]{1,6}[：:]", "", s)
    return _strip(s)


def change_pct(raw, body):
    if not raw or "【正文】" in raw or "【图1】" in raw:
        return None
    try:
        import zhconv
        raw = zhconv.convert(raw, "zh-cn")
    except ImportError:
        pass
    a, b = _strip(raw), _norm(body)
    if not a:
        return 0.0
    chg = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in
              difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op != "equal")
    return round(chg / len(a) * 100, 1)


# ---------------------------------------------------------------- 提示词

def _rules():
    txt = FORMAT_MD.read_text(encoding="utf-8") if FORMAT_MD.exists() else ""
    # 去掉给子任务看的文件路径 / 自检脚本部分
    txt = re.sub(r"(?s)## 写完自检.*", "", txt)
    return txt


SYSTEM = """你是一个中文视频转写稿的校对排版编辑。用户收藏了很多抖音、小红书视频，想在本地「当文章读」。
你要做的是把语音转写稿整理成阅读版：保留原话，只改错字、删口水词、补标点断句，再排版；并判断它属于哪个分类。

{rules}

## 这个学习库的分类（tab → 大类 → 已有小主题）
{tax}

补充：
- 每条都要填 `tab`（上面列的 tab 名之一）；内容是影视剧台词、时事八卦、体育新闻、广告招生、纯 vlog 无干货的，verdict 填 "move"、move_to 填 "其他"。
- category 原样抄上面的大类名，不要新起大类；topic 优先用已有的小主题。
- 另外给 3～5 个 `tags`（关键词，用于知识网络）。
- 改动上限：视频不超过 {limit}%。宁可少改，不要改写。
- 严格只输出一个 JSON 对象（不是数组），字段见上文「字段」一节，id 原样返回。"""

USER = """id：{id}
kind：{kind}
来源：{platform}
收藏夹分类：{folder}
title：{title}
author：{author}
transcript：
{text}"""


def _tax_text(t):
    lines = []
    desc = {x["name"]: x.get("desc", "") for x in t["tabs"]}
    for tab in [x["name"] for x in t["tabs"]]:
        lines.append(f"### {tab}：{desc.get(tab, '')}")
        for big in sorted(t["categories"].get(tab, {}), key=_cat_key):
            tops = t["categories"][tab][big]
            lines.append(f"- {big}：{'、'.join(tops[:25])}")
    return "\n".join(lines)


def polish(settings, it, log=print, force=False, topic=None):
    """润色一条。返回 (结果 dict, token 数)。结果 verdict = keep / low_info / move。
    topic：代刷来的条目是为哪个专题搜的；会顺便让 AI 判断和专题有没有关系，结果放在 res["topic_fit"]（True / False）。"""
    t = taxonomy()
    limit = float(settings.get("max_change_pct") or 15)
    raw = it.get("transcript") or ""
    kind = "图文" if it.get("media_type") == "note" else "视频"
    sys_msg = SYSTEM.format(rules=_rules(), tax=_tax_text(t), limit=int(limit))
    user = USER.format(id=it["id"], kind=kind, platform=config.PLATFORMS.get(it.get("platform") or "douyin"),
                       folder=it.get("fav_folder") or _folder_of(it.get("local_path")) or "未分类",
                       title=it.get("title") or "", author=it.get("author") or "", text=raw[:12000])
    if force:
        user += ("\n\n（用户已经看过这条，明确要收进学习库：verdict 只能是 keep，"
                 "照常改错字、去口水词、分段排版、归类，不要判成没信息量或其他。）")
    if topic:
        user += (f"\n\n（这条是为专题「{topic}」用关键词搜来的。请在 JSON 里另外加一个字段 \"topic_fit\"：内容和这个专题"
                 "有关系就写 true（讲到相关动作、要领、原理、练法、经验都算）；只有完全无关（搜索搜偏了、只是标签沾边、"
                 "讲的是别的东西）才写 false。）")
    messages = [{"role": "system", "content": sys_msg}, {"role": "user", "content": user}]
    used_total = 0
    res = None
    attempt, bad_json = 0, 0
    # 等多久：稿子越长，AI 把全文重排一遍输出得越久。短稿 5 分钟；每 1000 字多给约 2 分钟，最多 20 分钟
    # （原来固定 5 分钟，四五千字的长稿每次都超时）
    wait = min(1200, max(300, 180 + len(raw[:12000]) // 8))
    while True:
        text, used = llm.chat(settings, messages, max_tokens=8000, timeout=wait)
        used_total += used
        try:
            d = llm.parse_json(text)
        except Exception as e:
            bad_json += 1
            if bad_json >= 2:
                raise llm.LLMError("AI 两次返回的都不是有效的 JSON：" + (text or "")[:200], retry=True)
            # 常见原因：正文里有没转义的英文双引号、输出被截断。告诉它哪里错了，再要一次
            log(f"[{it['id']}] AI 返回的格式不对（{str(e)[:60]}），让它重新输出一次")
            messages += [{"role": "assistant", "content": (text or "")[:6000]},
                         {"role": "user", "content": "上面的输出不是合法的 JSON，程序读不了（" + str(e)[:80] + "）。"
                          "请重新输出完整的、只有一个 JSON 对象的结果：正文里引用别人说的话用中文引号“”或「」，"
                          "不要用英文双引号；换行写成 \\n；不要加 ``` 代码块标记，也不要在 JSON 前后加任何说明。"}]
            continue
        if isinstance(d, list):
            d = d[0] if d else {}
        res = _normalize(d, t)
        if topic:
            fit = d.get("topic_fit")
            res["topic_fit"] = (str(fit).strip().lower() not in ("false", "0", "no", "否")) if fit is not None else None
        if res["verdict"] != "keep":
            break
        pct = change_pct(raw, res["body"]) if kind == "视频" else None
        res["change_pct"] = pct
        if pct is None or pct <= limit or attempt:
            break
        attempt += 1
        log(f"[{it['id']}] 改动 {pct:.0f}%，超过 {limit:.0f}%，让 AI 改回原话再来一次")
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": f"这一版和原稿的逐字改动是 {pct:.0f}%，超过了 {limit:.0f}%。"
                      "请逐句对照原稿，把改写、换说法、调换语序、补充的地方都改回原话，只保留错字修正、删口水词和标点断句，"
                      "重新输出完整的 JSON。"}]
    if res["verdict"] == "keep":
        learn_topic(res["tab"], res["category"], res["topic"])
    return res, used_total


def _normalize(d, t):
    tabs = [x["name"] for x in t["tabs"]]
    v = str(d.get("verdict") or "keep").strip()
    if v not in ("keep", "low_info", "move"):
        v = "keep"
    tab = str(d.get("tab") or d.get("move_to") or "").strip()
    if v == "move" and tab in tabs:  # 挪到别的 tab = 照常收进学习库，只是换个分类
        v = "keep"
    res = {"mode": "yuedu", "verdict": v, "tab": tab if tab in tabs else (OTHER if v == "move" else tab),
           "theme": str(d.get("theme") or "").strip(), "reason": str(d.get("reason") or "").strip()}
    if v == "low_info":
        lt = str(d.get("low_info_type") or "其他").strip()
        res["low_info_type"] = lt if lt in LOW_TYPES else "其他"
        return res
    if v == "move":
        return res
    body = str(d.get("body") or "").strip()
    if not body:
        raise llm.LLMError("AI 没有返回正文", retry=True)
    if res["tab"] not in tabs:
        res["tab"] = tabs[0]
    gold = str(d.get("gold") or "").strip()
    if gold and gold.replace("**", "") not in body.replace("**", ""):
        gold = ""  # 金句必须是正文里的原话
    res.update(category=str(d.get("category") or "").strip() or "其他", topic=str(d.get("topic") or "").strip(),
               gold=gold, body=body, unsure=body.count("[?]"), note=str(d.get("note") or "").strip(),
               tags=[str(x).strip() for x in (d.get("tags") or []) if str(x).strip()][:6],
               title=res["theme"], summary=gold, points=[])
    return res


def _folder_of(p):
    if not p:
        return ""
    for lib in old_libs():
        try:
            rel = Path(p).resolve().relative_to(lib.resolve())
        except (ValueError, OSError):
            continue
        return rel.parts[0] if len(rel.parts) > 1 else ""
    return ""


# ---------------------------------------------------------------- 导入已有的润色结果（video-yuedu skill 的 done/）

def import_old(db, log=print):
    """把设置 old_yuedu_libs 里各个库的 _yuedu/done 结果写进学习库（按条目 id；已经是新版结果的不覆盖）。"""
    n = collections.Counter()
    for lib in old_libs():
        done = lib / "_yuedu" / "done"
        if not done.exists():
            continue
        res = {}
        for f in sorted(done.glob("*.json")):
            try:
                for x in json.loads(f.read_text(encoding="utf-8")):
                    res[int(x["id"])] = x
            except Exception:
                continue
        corr = {}
        cf = lib / "_yuedu" / "corrections.json"
        if cf.exists():
            try:
                corr = json.loads(cf.read_text(encoding="utf-8"))
            except Exception:
                pass
        for iid, x in res.items():
            it = db.get(iid)
            if not it or not it.get("local_path"):
                continue
            if not it.get("transcript") or it.get("status") not in ("done", "need_key", "skipped", "irrelevant"):
                continue  # 排队中 / 正在处理 / 清空了文字稿等重转的：不动，转好后下次再导入
            if os.path.normcase(str(it["local_path"])).find(os.path.normcase(str(lib))) != 0:
                continue  # id 对不上这个库（数据库换过）
            old = it.get("note") or {}
            if old.get("mode") == "yuedu" and not old.get("imported"):
                continue
            v = x.get("verdict") or "keep"
            tab = x.get("tab") or _folder_of(it["local_path"]) or ""
            if v == "move" and x.get("move_to") and x["move_to"] != OTHER:
                tab, v = x["move_to"], "keep"
            note = {"mode": "yuedu", "imported": str(lib), "verdict": v, "tab": tab,
                    "theme": x.get("theme") or "", "reason": x.get("reason") or ""}
            if v == "keep":
                body = x.get("body") or ""
                gold = x.get("gold") or ""
                for a, b in corr.get(str(iid), []):
                    body, gold = body.replace(a, b), gold.replace(a, b)
                note.update(category=x.get("category") or "其他", topic=x.get("topic") or "", gold=gold, body=body,
                            unsure=body.count("[?]"), note=x.get("note") or "", tags=[], title=x.get("theme") or "",
                            summary=gold, points=[],
                            change_pct=change_pct(it.get("transcript") or "", body)
                            if it.get("media_type") != "note" else None)
                db.update(iid, note=note, category=tab, status="done", reason=None)
                n["keep"] += 1
            elif v == "low_info":
                note["low_info_type"] = x.get("low_info_type") or "其他"
                db.update(iid, note=note, status="skipped", reason=f"没信息量：{note['low_info_type']}")
                n["low_info"] += 1
            else:
                db.update(iid, note=note, category=OTHER, status="irrelevant", reason="其他（不整理）")
                n["other"] += 1
    log(f"导入已有阅读稿：{n['keep']} 篇，没信息量 {n['low_info']} 条，其他 {n['other']} 条")
    return dict(n)


def forced_keep(it, raw=None, why=""):
    """用户点了「加入整理」但 AI 还是不收 / 没有说话内容：照原文收进学习库，正文不改。"""
    t = taxonomy()
    tabs = [x["name"] for x in t["tabs"]]
    title = (it.get("title") or "").strip() or "（无题）"
    raw = (raw if raw is not None else it.get("transcript") or "").strip()
    body = raw or "（这条视频几乎没有说话，内容主要在画面里，点上面的播放按钮看原视频。）"
    folder = it.get("fav_folder") or _folder_of(it.get("local_path")) or ""
    tab = folder if folder in tabs else (tabs[0] if tabs else "未分类")
    return {"mode": "yuedu", "verdict": "keep", "tab": tab, "theme": title[:60], "reason": why,
            "category": "其他", "topic": "我选的", "gold": "", "body": body, "unsure": 0, "note": "",
            "tags": [], "title": title[:60], "summary": "", "points": [], "forced": True}
