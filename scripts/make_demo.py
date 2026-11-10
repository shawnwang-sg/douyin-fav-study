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
    ("douyin", "爱好", "一、武术 · 太极", "步法", "猫步入门：先学会走路", "示例教练A",
     "上步先落脚跟，重心再慢慢跟过去。",
     "很多人一学猫步就把脚抬得很高，结果身体前后晃。\n\n## 先找走路的感觉\n上步先落脚跟，重心再慢慢跟过去。脚落下的时候是空的，"
     "像试探冰面一样。\n\n## 再加上转腰\n重心过来以后再转腰，把后脚带上来。整个过程要慢，**慢才能练出控制**。"),
    ("xhs", "爱好", "一、武术 · 太极", "步法", "下盘稳不稳，看收脚", "示例教练B",
     "收脚的时候膝盖别先动，让胯带着走。",
     "收脚是猫步里最容易被忽略的一半。\n\n收脚的时候膝盖别先动，让胯带着走，脚尖轻轻点地再收回来。"
     "\n\n可以扶着椅背练：一手扶稳，另一只脚慢慢收、慢慢出，每边十次。"),
    ("douyin", "爱好", "一、武术 · 太极", "站桩", "站桩十分钟，先把肩膀放下来", "示例教练C",
     "站桩不是站得越久越好，而是站得越松越好。",
     "站桩的第一步是放松肩膀。很多人站着站着肩膀就耸起来了。\n\n站桩不是站得越久越好，而是站得越松越好。"
     "\n\n1. 双脚与肩同宽\n2. 膝盖微屈，不超过脚尖\n3. 呼吸放慢，数到十再换气"),
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

# 专题：武术 › 太极 › 太极猫步（三条），带一段笔记
a = topics.node_create(None, "武术")
b = topics.node_create(a, "太极")
t = topics.topic_create(b, "太极猫步")
topics.items_add(t, ids[:3])
topics.topic_note(t, "【猫步入门：先学会走路】\n> 上步先落脚跟，重心再慢慢跟过去。\n\n练习计划：每天扶椅背收脚 10 次，再走 5 分钟猫步。")
# 精品、高亮
db.update(ids[0], starred=1)
db.update(ids[3], starred=1)
topics.hl_add(ids[0], "慢才能练出控制")

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
