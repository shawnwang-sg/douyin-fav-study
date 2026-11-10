"""学习库阅读页的数据部分：从数据库读出所有阅读稿，按「tab → 大类 → 小主题」排好，套用 reader_page 里原样复制的版面。"""
import collections
import html
import json
import re
import time
import urllib.parse
from pathlib import Path

from . import config, db, yuedu
from . import topics as topics_mod
from .reader_page import CSS, DELBTN, JS as _JS, LIMIT, PLAYBTN, cat_key, inline, render
from . import reader_topics as RT

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

# 删除记录存到学习库（data/deleted.json），换浏览器也在；同时保留原来的 localStorage
JS = _JS.replace(
    "const saveDel=()=>{try{localStorage.setItem(DKEY,JSON.stringify([...DEL]))}catch(e){}};",
    "const saveDel=()=>{try{localStorage.setItem(DKEY,JSON.stringify([...DEL]))}catch(e){}"
    "fetch('/api/library/deleted',{method:'POST',headers:{'Content-Type':'application/json'},"
    "body:JSON.stringify({ids:[...DEL].map(Number)})}).catch(()=>{})};").replace(
    "<td><button class=del data-restore=1>恢复</button></td>",
    "<td style=white-space:nowrap><button class=del data-restore=1>恢复</button> "
    "<button class=purge title='删掉记录和笔记（本地视频文件不动）；以后代刷不会再刷到它，同步收藏、手动添加仍可以加回来'>彻底删除</button></td>").replace(
    # 回收站里写清楚原来在哪：分类 › 大类 › 小主题（恢复就回到那里）；代刷来的标「代刷」
    """function where(el){if(el.tagName==='ARTICLE'){const s=el.closest('section.tab'),b=$(`.tabs button[data-tab="${s.id}"]`);
 return '阅读 · '+(b?b.firstChild.textContent:'')}return '待处理'}""",
    """function where(el){if(el.tagName==='ARTICLE'){const s=el.closest('section.tab'),b=$(`.tabs button[data-tab="${s.id}"]`);
 let h2='',h3='';for(let n=el.previousElementSibling;n;n=n.previousElementSibling){if(!h3&&n.tagName==='H3')h3=n.firstChild.textContent.trim();
  if(n.tagName==='H2'){h2=n.firstChild.textContent.trim();break}}
 const sr=typeof PRE_SEARCH!=='undefined'&&PRE_SEARCH.includes(+el.dataset.id);
 return (sr?'代刷 · ':'')+[b?b.firstChild.textContent:'',h2,h3].filter(Boolean).join(' › ')}return '待处理'}""")
assert "PRE_SEARCH" in JS, "回收站 where() 没替换上"

TOPBAR_CSS = """
.topbar{display:flex;align-items:center;gap:4px 18px;flex-wrap:wrap;font-size:14px;margin:-8px 0 14px;
 font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
.topbar a{color:var(--mut);text-decoration:none}.topbar a:hover{color:var(--acc)}.topbar b{color:var(--fg);letter-spacing:.04em}
.topbar a.cur{color:var(--fg);font-weight:600}.topbar .sp{flex:1}
.src .pf{font-size:12px;border:1px solid var(--line);border-radius:999px;padding:0 8px;margin-left:8px;color:var(--mut)}
.src .nt{font-size:12px;margin-left:10px;color:var(--mut);text-decoration:none}.src .nt:hover{color:var(--acc)}
.gotop{position:fixed;right:22px;bottom:22px;z-index:8;display:flex;flex-direction:column;gap:8px}
.gotop button{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:999px;padding:6px 14px;
 font:14px/1.5 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.12)}
.gotop button:hover{color:var(--acc);border-color:var(--acc)}
@media (max-width:600px){.gotop{right:12px;bottom:12px}.gotop button{padding:5px 11px;font-size:13px}}
"""
GOTOP = """<div class=gotop><button id=toSec title=回到当前大类的开头>↑ 本章节最上</button><button id=toTop title=回到页面最上面>⇈ 最上</button></div>
<script>(()=>{
const how=d=>Math.abs(d)>3*innerHeight?'auto':'smooth';
document.getElementById('toTop').onclick=()=>window.scrollTo({top:0,behavior:how(scrollY)});
document.getElementById('toSec').onclick=()=>{
 const tab=document.querySelector('section.tab.on');if(!tab)return;
 const hs=[...tab.querySelectorAll('h2')].filter(h=>h.offsetParent);let cur=null;
 for(const h of hs){if(h.getBoundingClientRect().top<=90)cur=h;else break}
 const el=cur||tab;el.scrollIntoView({behavior:how(el.getBoundingClientRect().top),block:'start'})};
})()</script>"""
DELETED_PATH = config.DATA / "deleted.json"


def search_ids():
    """代刷搜来的条目：默认放回收站，不进收藏分类（只在专题里看）；在回收站点「恢复」才进收藏。"""
    return {r[0] for r in db.conn().execute("SELECT id FROM items WHERE source='search'")}


def deleted_ids():
    ids = set(search_ids())
    for f in [DELETED_PATH] + [lib / "_yuedu" / "deleted.json" for lib in yuedu.old_libs()]:
        try:
            ids |= {int(i) for i in json.loads(f.read_text(encoding="utf-8")).get("ids", [])}
        except Exception:
            pass
    return sorted(ids)


def save_deleted(ids):
    """回收站里现在的全部 id（页面每次删除 / 恢复都会发完整列表）。旧库的 deleted.json 只读不改。
    默认就在回收站里的（旧库删过的、代刷来的），不在这次列表里 = 用户恢复了，记进 restored。"""
    old = set(search_ids())
    for lib in yuedu.old_libs():
        try:
            old |= {int(i) for i in json.loads((lib / "_yuedu" / "deleted.json").read_text(encoding="utf-8")).get("ids", [])}
        except Exception:
            pass
    ids = {int(i) for i in ids}
    DELETED_PATH.write_text(json.dumps({"ids": sorted(ids), "restored": sorted(old - ids)}), encoding="utf-8")


def _restored():
    try:
        return {int(i) for i in json.loads(DELETED_PATH.read_text(encoding="utf-8")).get("restored", [])}
    except Exception:
        return set()


def _topic_map():
    tmap = {}
    for lib in yuedu.old_libs():
        try:
            for big, m in json.loads((lib / "_yuedu" / "topics.json").read_text(encoding="utf-8")).items():
                tmap.setdefault(big, {}).update(m)
        except Exception:
            pass
    return tmap


def media_link(x):
    """「原视频」：页内弹窗播放本地视频（/media/<id>）；图文弹出本地图片。本地文件不在时退回线上链接。"""
    p = Path(x.get("_path") or "")
    try:
        exists = bool(x.get("_path")) and p.exists()
    except OSError:
        exists = False
    if exists:
        if p.is_dir():
            imgs = sorted(f.name for f in p.iterdir() if f.suffix.lower() in IMG_EXT)
            if imgs:
                srcs = json.dumps([f"/media/{x['id']}/{urllib.parse.quote(n)}" for n in imgs])
                return f"<a href='#' class=play data-imgs='{html.escape(srcs)}'>原图文</a>"
        else:
            return f"<a href='#' class=play data-video='/media/{x['id']}'>原视频</a>"
    if x.get("_url"):
        label = "小红书原链接" if x.get("_platform") == "xhs" else "抖音原链接"
        return f"<a href='{html.escape(x['_url'])}' target=_blank rel=noopener>{label}</a>"
    return ""


def row(x):
    return (f"<tr data-id='{x['id']}'><td>#{x['id']}</td><td>{html.escape(x.get('_folder', ''))}</td>"
            f"<td>{html.escape(x.get('theme') or '')}</td><td>{html.escape(x.get('_author', ''))}</td>"
            f"<td>{html.escape(x.get('reason') or '')}</td><td>{media_link(x)}</td><td>{DELBTN}</td></tr>")


def build():
    """生成整页 HTML（字符串）。"""
    t = yuedu.taxonomy()
    tab_names = [x["name"] for x in t["tabs"]]
    tmap = _topic_map()
    rows = db.conn().execute(
        "SELECT id,title,author,url,local_path,platform,media_type,status,reason,category,note_json "
        "FROM items WHERE status IN ('done','skipped','irrelevant')").fetchall()
    keep = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(list)))
    low, move, qc = [], [], []
    for r in rows:
        n = json.loads(r["note_json"] or "null") or {}
        x = dict(n)
        x.update(id=r["id"], _author=r["author"] or "", _url=r["url"] or "", _path=r["local_path"] or "",
                 _platform=r["platform"] or "douyin")
        if n.get("mode") != "yuedu":
            if r["status"] == "skipped" and "说话内容" in (r["reason"] or ""):
                low.append({**x, "verdict": "low_info", "low_info_type": "画面为主",
                            "reason": "转写时几乎没听到说话（画面、字幕或纯音乐为主）",
                            "theme": re.sub(r"#\S+", "", r["title"] or "").strip()[:40], "_folder": ""})
            continue
        v = n.get("verdict")
        x["_folder"] = n.get("tab") or r["category"] or ""
        if r["status"] == "done" and v == "keep":
            x["topic"] = tmap.get(x.get("category"), {}).get(x.get("topic"), x.get("topic"))
            x["_pct"] = n.get("change_pct")
            keep[x["_folder"] or "未分类"][x.get("category") or "未分类"][x.get("topic") or "其他"].append(x)
            qc.append(x)
        elif v == "move" or r["status"] == "irrelevant":
            x.setdefault("move_to", yuedu.OTHER)
            move.append(x)
        else:
            low.append(x)

    title = "学习库"
    hidden = set(deleted_ids()) - _restored()  # 在回收站里的（含代刷来的）：不算进总数和分类条数
    P = ["<!doctype html><html lang=zh><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>",
         f"<title>{title} · 收藏学习台</title><link rel=icon href='/static/logo.svg'><style>{CSS}{TOPBAR_CSS}{RT.CSS}</style><body><main>",
         "<nav class=topbar><b>收藏学习台</b><a href='/#/process'>添加与处理</a><a class=cur href='/reader'>学习库</a>"
         "<a href='/#/graph'>知识网络</a><a href='/#/weekly'>每周周报</a><span class=sp></span><a href='/#/settings'>设置</a></nav>",
         f"<header><h1>{title}</h1>",
         f"<p class=sub>抖音 + 小红书收藏 · 视频转写稿 · 去口水词、改错字、不改原话 · 共 {sum(1 for x in qc if x['id'] not in hidden)} 篇 · "
         f"更新于 {time.strftime('%Y-%m-%d %H:%M')}</p>",
         "<input id=q type=search placeholder='搜索标题、金句、作者……' autocomplete=off></header><nav class=tabs>"]
    order = tab_names + sorted(k for k in keep if k not in tab_names)
    tabs = [f for f in order if keep.get(f)] + [f for f in order if not keep.get(f)]
    P.append(RT.STAR_BUTTON + RT.TAB_BUTTON)  # 特殊 tab：精品、专题整理放最前
    for i, f in enumerate(tabs):
        cnt = sum(1 for tt in keep.get(f, {}).values() for v in tt.values() for x in v if x["id"] not in hidden)
        P.append(f"<button data-tab='t{i}' class='{'on' if i == 0 else ''}'>{html.escape(f)}<span>{cnt or '待整理'}</span></button>")
    P.append(f"<button data-tab=tlow class=sptab>待处理清单<span>{len(low) + len(move)}</span></button>"
             "<button data-tab=tbin class=sptab>回收站<span id=bincnt>0</span></button><button data-tab=tqc class=sptab>质检</button></nav>")

    for i, f in enumerate(tabs):
        cats = keep.get(f, {})
        P.append(f"<section id=t{i} class='tab{' on' if i == 0 else ''}'>")
        if not cats:
            P.append("<p class=empty>这个分类还没有整理好的文章。</p></section>")
            continue
        F = html.escape(f)
        P.append("<div class=layout><aside class=side><div class=side-in>")
        for cat in sorted(cats, key=cat_key):
            topics, C = cats[cat], html.escape(cat)
            total = sum(len(v) for v in topics.values())
            P.append(f"<div class=tc><a href='#{F}-{C}' class=tcat>{C}<span>{total}</span></a><div class=ttop>")
            for tp in sorted(topics, key=lambda k: -len(topics[k])):
                T = html.escape(tp)
                P.append(f"<a href='#{F}-{C}-{T}' class=tt>{T}<span>{len(topics[tp])}</span></a><div class=tart>")
                for x in sorted(topics[tp], key=lambda x: x["id"]):
                    P.append(f"<a href='#a{x['id']}' data-id='{x['id']}'>{html.escape(re.sub(r'[*]', '', x.get('theme') or '（无题）'))}</a>")
                P.append("</div>")
            P.append("</div></div>")
        P.append(f"</div></aside><div class=content><div class=tabplay>「{F}」全部{PLAYBTN}"
                 "<label class=wi><input type=checkbox class=withimg checked> 连播带图文</label></div>")
        for cat in sorted(cats, key=cat_key):
            topics, C = cats[cat], html.escape(cat)
            P.append(f"<h2 id='{F}-{C}'>{C}{PLAYBTN}</h2>")
            for tp in sorted(topics, key=lambda k: -len(topics[k])):
                T = html.escape(tp)
                P.append(f"<h3 id='{F}-{C}-{T}'>{T}{PLAYBTN}</h3>")
                for x in sorted(topics[tp], key=lambda x: x["id"]):
                    pf = "小红书" if x["_platform"] == "xhs" else "抖音"
                    s = html.escape((x.get("theme", "") + x.get("gold", "") + x["_author"] + pf).lower())
                    P.append(f"<article id='a{x['id']}' data-id='{x['id']}' data-s='{s}'>"
                             f"<div class=hd><h5>{inline(x.get('theme', ''))}</h5>{media_link(x)}</div>"
                             f"<div class=src><span>—— {html.escape(x['_author'] or '佚名')}<span class=pf>{pf}</span>"
                             f"<a class=nt href='/#/item/{x['id']}' title='笔记详情：精读、自测、我的笔记'>笔记 ›</a></span>{DELBTN}</div>"
                             f"<div class=gold>{inline(x.get('gold', ''))}</div><div class=body>{render(x.get('body', ''))}</div></article>")
        P.append("</div></div></section>")

    P.append(RT.STAR_SECTION + RT.SECTION)
    P.append("<section id=tlow class=tab><h2>文字没有信息量的</h2><p class=note>这些不进阅读版。原文件都还在，需要的话可以回原视频看画面。</p>")
    by = collections.defaultdict(list)
    for x in low:
        by[x.get("low_info_type") or "其他"].append(x)
    std = ["画面为主", "断续口播", "纯音乐歌词", "残缺", "其他"]
    for tp in std + sorted(k for k in by if k not in std):
        if by.get(tp):
            P.append(f"<h3>{html.escape(tp)}<span class=cnt>{len(by[tp])}</span></h3><table>")
            P += [row(x) for x in sorted(by[tp], key=lambda x: (x["_folder"], x["id"]))]
            P.append("</table>")
    P.append("<h2>不整理的（其他）</h2><p class=note>影视剧台词、时事、体育新闻、广告招生等，只列在这里，不收进阅读版。</p>")
    bym = collections.defaultdict(list)
    for x in move:
        bym[x.get("move_to") or yuedu.OTHER].append(x)
    for tp in sorted(bym):
        P.append(f"<h3>建议移到「{html.escape(tp)}」<span class=cnt>{len(bym[tp])}</span></h3><table>")
        P += [row(x) for x in sorted(bym[tp], key=lambda x: x["id"])]
        P.append("</table>")
    P.append("</section>")

    vids = [x for x in qc if x.get("_pct") is not None]
    over = sorted([x for x in vids if x["_pct"] > LIMIT], key=lambda x: -x["_pct"])
    pcts = sorted(x["_pct"] for x in vids)
    mid = pcts[len(pcts) // 2] if pcts else 0
    uns = sum(int(x.get("unsure") or 0) for x in qc)
    P.append(f"<section id=tqc class=tab><h2>质检</h2><p class=note>改动比例 = 去掉标点和排版标记后，和原始转写逐字比对的差异。"
             f"超过 15% 的多半是转写错字多（改错字也算改动）。图文不参与统计。</p><ul class=stat><li>阅读版 {len(qc)} 篇（视频 {len(vids)}）</li>"
             f"<li>改动中位数 {mid:.1f}%</li><li>超过 15% 的 {len(over)} 篇</li><li>存疑 [?] 共 {uns} 处</li></ul><h3>改动超过 15% 的</h3><table>")
    for x in over:
        P.append(f"<tr><td>#{x['id']}</td><td>{html.escape(x.get('theme', ''))}</td><td>{html.escape(x['_author'])}</td>"
                 f"<td>{x['_pct']:.0f}%</td><td>{html.escape(x.get('note') or '')}</td></tr>")
    P.append("</table></section>")
    P.append("<section id=tbin class=tab><h2>回收站</h2><p class=note>在阅读页或待处理清单里点「删除」的条目都在这里，原文件不动，"
             "点「恢复」放回原处。删除记录存在学习库里（data/deleted.json），换浏览器也在。</p>"
             "<p><button id=binexp class=del>导出清单</button> <button id=binpurge class=purge>全部彻底删除</button></p><table id=bintab></table><p id=binempty class=empty>回收站是空的。</p></section>")
    pre = sorted(set(deleted_ids()) - _restored())
    starred = [r[0] for r in db.conn().execute("SELECT id FROM items WHERE starred=1")]  # 精品
    hl = json.dumps(topics_mod.highlights(), ensure_ascii=False).replace("</", "<\\/")  # 高亮过的句子
    P.append(f"</main>{GOTOP}{RT.HTML}<script>const PRE_DEL={json.dumps(pre)},PRE_STAR={json.dumps(starred)},PRE_SEARCH={json.dumps(sorted(search_ids()))},PRE_HL={hl},PAGE_NAME='学习库';</script>" + JS
             + RT.JS.replace("${PLAYBTN_HTML}", PLAYBTN))
    return "".join(P)
