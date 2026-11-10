"""生成一份演示数据（几篇示例文章、一个专题、精品、高亮、笔记），用来试用或截图，不碰你自己的数据。

用法（在程序目录下）：
    python scripts/make_demo.py demo_data            # 生成到 demo_data/
    FAVSTUDY_DATA=demo_data python -m app --port 8770 # 用演示数据启动（Windows PowerShell：$env:FAVSTUDY_DATA="demo_data"）

文章内容是编的示例文字，不对应真实视频，「原视频」按钮放不出来。
"""
import os
import sys
from pathlib import Path

target = Path(sys.argv[1] if len(sys.argv) > 1 else "demo_data").resolve()
if (target / "library.db").exists():
    sys.exit(f"{target} 里已经有数据了，换一个空文件夹")
os.environ["FAVSTUDY_DATA"] = str(target)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, db, topics, yuedu  # noqa: E402  (要在设好 FAVSTUDY_DATA 之后再导入)

db.init()
topics.init()
yuedu.save_taxonomy({"tabs": yuedu.DEFAULT_TABS, "categories": {}})

ARTICLES = [
    # (平台, 分类 tab, 大类, 小主题, 标题, 作者, 金句, 正文)
    ("douyin", "爱好", "一、网球", "正手", "正手引拍：先转肩，再拉拍", "示例教练A",
     "先转肩，球拍自然就到位了。",
     "很多人正手一上来就用手臂往后拉拍，结果击球总是晚。\n\n## 先转肩\n看到球过来，先转肩，球拍自然就到位了。"
     "左手扶着拍颈一起转，肩膀转到侧对球网。\n\n## 再把拍头放下去\n拍头自然下落，再从下往上刷出去。记住一句话：**转肩比拉拍重要**。"),
    ("xhs", "爱好", "一、网球", "正手", "击球点要在身体前面", "示例教练B",
     "球打得晚，多半不是挥拍慢，而是准备晚。",
     "击球点在身体前面一个小臂的距离，这时候手臂最有力。\n\n球打得晚，多半不是挥拍慢，而是准备晚。"
     "\n\n可以对着墙练：球弹起来的时候喊「弹」，击球的时候喊「打」，喊得出节奏，击球点就靠前了。"),
    ("douyin", "爱好", "一、网球", "发球", "发球抛球：像把球放到架子上", "示例教练C",
     "抛球不是扔，是把球送上去。",
     "发球不稳，十次有八次是抛球的问题。\n\n抛球不是扔，是把球送上去，像把球放到头顶前面的一个架子上。"
     "\n\n1. 用指根托球，不要用手心握\n2. 手臂伸直往上送，到眼睛的高度再松手\n3. 球落下来应该砸在前脚的前面一点"),
    ("douyin", "厉害", "一、学习方法", "复盘", "每天睡前花五分钟复盘", "示例作者D",
     "复盘不是回忆做了什么，而是想明天怎么做得更好。",
     "复盘不是回忆做了什么，而是想明天怎么做得更好。\n\n## 三个问题\n1. 今天哪件事做得最好？\n2. 哪件事可以做得更好？\n"
     "3. 明天只改一件事，改哪件？"),
    ("xhs", "美食", "一、家常菜", "快手菜", "十分钟番茄炒蛋，蛋要先炒", "示例厨师E",
     "鸡蛋先炒到七成熟盛出来，最后再回锅。",
     "番茄炒蛋好吃的关键是鸡蛋嫩。\n\n鸡蛋先炒到七成熟盛出来，最后再回锅。番茄切小块，加一点糖提鲜，"
     "炒出汁以后再把鸡蛋倒回去，翻两下就出锅。"),
    ("douyin", "身体健康", "一、睡眠", "作息", "睡不着的时候别看时间", "示例医生F",
     "越看时间越焦虑，越焦虑越睡不着。",
     "半夜醒来第一件事别看手机、别看时间。\n\n越看时间越焦虑，越焦虑越睡不着。可以起来坐一会儿，等困了再回床上。"),
]

ids = []
for n, (plat, tab, cat, tp, title, author, gold, body) in enumerate(ARTICLES, 1):
    aid = f"{7000000000000000000 + n}" if plat == "douyin" else f"{n:024x}"
    iid, _ = db.add("favorite", aweme_id=aid, url="", title=title, author=author, platform=plat)
    note = {"mode": "yuedu", "verdict": "keep", "tab": tab, "category": cat, "topic": tp, "theme": title,
            "title": title, "gold": gold, "summary": gold, "body": body, "change_pct": 6.0, "unsure": 0,
            "tags": [], "points": [], "reason": "", "note": ""}
    db.update(iid, status="done", note=note, category=tab, transcript=body, duration=120)
    yuedu.learn_topic(tab, cat, tp)
    ids.append(iid)

# 专题：网球 › 基本功 › 正手和发球（三条），带一段笔记
a = topics.node_create(None, "网球")
b = topics.node_create(a, "基本功")
t = topics.topic_create(b, "正手和发球")
topics.items_add(t, ids[:3])
topics.topic_note(t, "> 先转肩，球拍自然就到位了。\n\n练习计划：每天对墙打正手 50 个，再练 20 个抛球。")
topics.quote_add(t, ids[0], "先转肩，球拍自然就到位了。")  # 摘抄来自哪篇：笔记里不写出处，记在后台
# 精品、高亮
db.update(ids[0], starred=1)
db.update(ids[3], starred=1)
topics.hl_add(ids[0], "转肩比拉拍重要")

# 几条被跳过的（「添加与处理 → 已跳过」按原因分组）
SKIPPED = [
    ("douyin", "示例：一段纯音乐的风景视频", "视频里几乎没有说话内容（可能是纯音乐或无对白）"),
    ("xhs", "示例：一张图配一句话", "没信息量：画面为主"),
    ("douyin", "示例：90 分钟的直播回放", "超过 15 分钟的长视频，暂不转写，已单独存放"),
    ("xhs", "示例：说到一半就断了", "没信息量：残缺"),
]
for k, (plat, title, reason) in enumerate(SKIPPED, 50):
    aid = f"{7000000000000000000 + k}" if plat == "douyin" else f"{k:024x}"
    iid, _ = db.add("favorite", aweme_id=aid, url="", title=title, author="示例作者", platform=plat)
    db.update(iid, status="skipped", reason=reason)
print(f"演示数据已生成：{target}（{len(ids)} 篇文章、{len(SKIPPED)} 条已跳过、1 个专题）")
