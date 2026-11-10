"""学习库 = 阅读页（完全沿用 video-yuedu skill 的 yuedu.html 版面）：顶部 tab = 统一分类，左侧目录 大类 → 小主题 → 每篇，
文章默认展开，搜索、原视频弹窗播放、连播 / 洗牌、待处理清单、回收站、质检。抖音和小红书合在一起，不区分。

和 skill 生成的静态页的区别只有：数据直接从学习库数据库读（每次打开都是最新的），本地视频走 /media/<id> 播放，
顶部多一条回到「添加与处理 / 知识网络 / 周报 / 设置」的导航。版面相关的 CSS / JS 原样复制自 build_yuedu.py。
"""
import collections
import html
import json
import re
import time
import urllib.parse
from pathlib import Path

from . import config, db, yuedu

LIMIT = 15
CN_NUM = "一二三四五六七八九十"
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def cat_key(c):
    m = re.match(r"^([一二三四五六七八九十]+)、", c or "")
    if m:
        s = m.group(1)
        n = 10 + (CN_NUM.index(s[1]) + 1 if len(s) > 1 else 0) if s.startswith("十") else CN_NUM.index(s[0]) + 1
        return (0, n, c)
    return (1, 0, c or "未分类")


def inline(s):
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    return s.replace("[?]", "<mark title='听不清，按上下文推测'>[?]</mark>")


def render(body):
    out, lst, quote = [], None, []

    def flush():
        nonlocal lst, quote
        if lst:
            out.append(f"<{lst[0]}>" + "".join(f"<li>{inline(x)}</li>" for x in lst[1]) + f"</{lst[0]}>")
            lst = None
        if quote:
            out.append("<blockquote>" + "".join(f"<p>{x}</p>" for x in quote) + "</blockquote>")
            quote = []

    for line in body.split("\n"):
        if not line.strip():
            flush(); continue
        m = re.match(r"^(\d+)\. (.*)", line)
        if m or line.startswith("- "):
            tag = "ol" if m else "ul"
            if lst and lst[0] != tag:
                flush()
            if quote:
                flush()
            lst = lst or (tag, [])
            lst[1].append(m.group(2) if m else line[2:]); continue
        if line.startswith("> "):
            if lst:
                flush()
            t = line[2:]
            sp = re.match(r"^([^：:]{1,6})[：:](.*)", t)
            if sp and len(sp.group(1)) <= 4 and not re.search(r"[，。]", sp.group(1)):
                quote.append(f"<span class=who>{html.escape(sp.group(1))}</span>{inline(sp.group(2))}")
            else:
                quote.append(inline(t))
            continue
        flush()
        out.append(f"<h4>{inline(line[3:])}</h4>" if line.startswith("## ") else f"<p>{inline(line)}</p>")
    flush()
    return "".join(out)



DELBTN = "<button class=del title=移到回收站（不删文件，可恢复）>删除</button>"

CSS = """
:root{--bg:#f7f4ee;--fg:#24211c;--mut:#7a7368;--card:#fffdf9;--line:#e6dfd3;--acc:#9a5b2a;--q:#f1ebe0;--on:#24211c;--onfg:#fffdf9}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#181614;--fg:#ebe6dd;--mut:#a69f94;--card:#211e1b;--line:#38332d;--acc:#e0a46b;--q:#2a2622;--on:#ebe6dd;--onfg:#181614}}
:root[data-theme=dark]{--bg:#181614;--fg:#ebe6dd;--mut:#a69f94;--card:#211e1b;--line:#38332d;--acc:#e0a46b;--q:#2a2622;--on:#ebe6dd;--onfg:#181614}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:18px/1.95 "Noto Serif SC","Source Han Serif SC","Songti SC","SimSun",serif}
main{max-width:1680px;margin:0 auto;padding:28px 32px 100px}a{color:var(--acc)}
header{max-width:none}#q{max-width:560px}
.layout{display:grid;grid-template-columns:340px minmax(0,1fr);gap:36px;align-items:start}
.side{position:sticky;top:64px;max-height:calc(100vh - 76px);overflow-y:auto;padding:14px 4px 30px 0;font-size:15px;line-height:1.7;border-right:1px solid var(--line)}
.side .tc{padding:8px 10px 8px 0;border-bottom:1px dashed var(--line)}.side .tc:last-child{border:0}
.side .tcat{display:block;font-weight:600;color:var(--fg);text-decoration:none}.side .tcat:hover,.side .ttop a:hover{color:var(--acc)}
.side .ttop{display:block;margin:2px 0 0 12px}.side .ttop a{display:block;color:var(--mut);text-decoration:none;padding:1px 0}
.side span{color:var(--mut);font-size:12px;margin-left:6px;font-weight:400}
.side .ttop a.tt{color:var(--fg);margin-top:4px}
.side .tart{margin:0 0 4px 12px;border-left:1px solid var(--line);padding-left:8px}
.side .tart a{font-size:13px;line-height:1.55;padding:2px 0;color:var(--mut);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.side .tart a.cur{color:var(--acc)}
button.del{border:1px solid var(--line);background:none;color:var(--mut);border-radius:999px;padding:0 9px;font:inherit;font-size:12px;line-height:1.7;cursor:pointer}
button.del:hover{color:#b3261e;border-color:#b3261e}
.gone{display:none!important}
#bintab td:nth-child(2){white-space:nowrap;color:var(--mut)}
.content h2:first-child{margin-top:8px}h2,h3{scroll-margin-top:70px}
header h1{font-size:30px;letter-spacing:.06em;margin:0}.sub{color:var(--mut);font-size:14px;margin:4px 0 14px}
#q{width:100%;padding:9px 14px;border:1px solid var(--line);border-radius:999px;background:var(--card);color:var(--fg);font:inherit;font-size:15px}
.tabs{position:sticky;top:0;z-index:5;background:var(--bg);display:flex;gap:6px;overflow-x:auto;padding:12px 0;margin:8px 0 6px;border-bottom:1px solid var(--line)}
.tabs button{flex:none;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:999px;padding:5px 14px;font:inherit;font-size:15px;cursor:pointer}
.tabs button span{margin-left:6px;font-size:12px;color:var(--mut)}.tabs button.on{background:var(--on);color:var(--onfg);border-color:var(--on)}.tabs button.on span{color:inherit;opacity:.7}
.tab{display:none}.tab.on{display:block}.empty,.note{color:var(--mut);font-size:15px}
.toc{border:1px solid var(--line);border-radius:12px;padding:14px 18px;margin:18px 0;font-size:15px;background:var(--card)}
.tc{padding:6px 0;border-bottom:1px dashed var(--line)}.tc:last-child{border:0}.tcat{font-weight:600;color:var(--fg);text-decoration:none}
.toc span,.cnt{color:var(--mut);font-size:12px;margin-left:4px;font-weight:400}.ttop{display:flex;flex-wrap:wrap;gap:2px 14px;margin-top:2px}.ttop a{text-decoration:none;color:var(--mut)}
h2{font-size:24px;margin:44px 0 6px;color:var(--acc);letter-spacing:.04em}h3{font-size:18px;margin:26px 0 10px;color:var(--mut);font-weight:600}
article{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:22px 26px;margin:14px 0}
article h5{font-size:20px;margin:0;line-height:1.5}.src{color:var(--mut);font-size:13px;margin:2px 0 12px;display:flex;align-items:center;justify-content:space-between;gap:16px}
.src button.del{flex:none;opacity:.55}.src button.del:hover{opacity:1}
.hd{display:flex;align-items:center;flex-wrap:wrap;gap:6px 18px}
.hd>a{flex:none;display:inline-flex;align-items:center;gap:6px;padding:7px 20px;border-radius:999px;background:var(--acc);color:var(--card);
 text-decoration:none;font-size:16px;font-weight:600;line-height:1.4;box-shadow:0 1px 3px rgba(0,0,0,.15)}
.hd>a::before{content:"▶";font-size:13px}.hd>a[data-imgs]::before{content:"🖼"}.hd>a:hover{filter:brightness(1.1)}
.gold{border-left:3px solid var(--acc);padding:2px 0 2px 14px;color:var(--acc);font-size:17px;line-height:1.8}
details{margin-top:12px}summary{cursor:pointer;color:var(--mut);font-size:14px;list-style:none}summary::before{content:"▸ "}details[open] summary::before{content:"▾ "}
details[open] summary{margin-bottom:12px}
article p{margin:0 0 1.05em;text-align:justify}article h4{font-size:17px;margin:1.5em 0 .5em;color:var(--acc)}
blockquote{margin:0 0 1.1em;padding:12px 18px;background:var(--q);border-radius:8px}blockquote p{margin:0 0 .5em}blockquote p:last-child{margin:0}
.who{display:inline-block;min-width:2.6em;color:var(--acc);font-weight:600}ol,ul{padding-left:1.4em;margin:0 0 1.1em}li{margin:.35em 0}
mark{background:none;color:var(--mut);font-size:.8em}
table{width:100%;border-collapse:collapse;font-size:14px;line-height:1.6;margin-bottom:10px}td{border-bottom:1px solid var(--line);padding:6px 6px;vertical-align:top}td:first-child{color:var(--mut);white-space:nowrap}
.stat{display:flex;flex-wrap:wrap;gap:8px 22px;padding:0;list-style:none;font-size:15px}
.hide{display:none!important}
.gold{margin-bottom:16px}
@media (max-width:900px){main{padding:20px 16px 80px}.layout{grid-template-columns:1fr;gap:12px}
 .side{position:static;max-height:none;border-right:0;border:1px solid var(--line);border-radius:12px;padding:10px 14px;background:var(--card)}
 .side .ttop{display:flex;flex-wrap:wrap;gap:0 12px;margin-left:0}}
@media (max-width:600px){article{padding:18px 16px}body{font-size:17px}table{font-size:13px}}
#pl{border:0;padding:0;background:#000;border-radius:12px;max-width:min(96vw,1100px);max-height:94vh;overflow:hidden}
#pl::backdrop{background:rgba(0,0,0,.72)}
#pl .x{position:absolute;top:8px;right:10px;z-index:2;border:0;background:rgba(0,0,0,.55);color:#fff;font-size:22px;width:36px;height:36px;border-radius:50%;cursor:pointer}
#pl video{display:block;max-width:min(96vw,1100px);max-height:94vh}
#pl .imgs{max-height:94vh;overflow-y:auto;background:var(--bg);padding:10px}#pl .imgs img{display:block;max-width:100%;margin:0 auto 10px}
.play{cursor:pointer}
.pp{display:inline-flex;gap:6px;margin-left:14px;vertical-align:middle}
.pp button{border:1px solid var(--line);background:var(--card);color:var(--mut);border-radius:999px;padding:1px 11px;font:inherit;font-size:13px;line-height:1.7;cursor:pointer;letter-spacing:0;font-weight:400}
.pp button:hover{color:var(--acc);border-color:var(--acc)}
.tabplay{font-size:14px;color:var(--mut);margin:8px 0 -18px}.wi{margin-left:16px;cursor:pointer;white-space:nowrap}.wi input{vertical-align:middle;accent-color:var(--acc)}
#pl .bar{display:none;align-items:center;gap:10px;padding:8px 12px;background:#111;color:#ddd;font-size:14px}
#pl.list .bar{display:flex}#pl.list video{max-height:calc(94vh - 46px)}#pl.list .imgs{max-height:calc(94vh - 46px)}
#pl .bar .info{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#pl .bar button{border:1px solid #444;background:#222;color:#eee;border-radius:999px;padding:2px 12px;font:inherit;font-size:13px;cursor:pointer}
article.now{outline:2px solid var(--acc);outline-offset:3px}
.side .fx{border:0;background:none;color:var(--mut);cursor:pointer;font:inherit;font-size:12px;width:16px;padding:0;margin-left:-16px;vertical-align:baseline}
.side .tc,.side .ttop{padding-left:16px}.side .ttop{margin-left:0}.side .fx:hover{color:var(--acc)}
.side .fold-c>.ttop,.side .fold-t+.tart{display:none}.side .fx+a.tcat,.side .fx+a.tt{display:inline-block}
.side .foldall{display:flex;gap:8px;margin:0 0 6px 0;font-size:12px}.side .foldall button{border:1px solid var(--line);background:var(--card);color:var(--mut);border-radius:999px;padding:1px 10px;cursor:pointer;font:inherit}
.side .foldall button:hover{color:var(--acc);border-color:var(--acc)}
"""
PLAYBTN = "<span class=pp><button class=pa data-mode=seq title=按顺序连续播放>▶ 连播</button><button class=pa data-mode=shuf title=随机顺序连续播放>🔀 洗牌</button></span>"
JS = """<dialog id=pl><button class=x aria-label=关闭>×</button><div class=stage></div><div class=bar><button class=prev>↑ 上一条</button><span class=info></span><button class=next>下一条 ↓</button></div></dialog><script>
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
$$('.tabs button').forEach(b=>b.onclick=()=>{$$('.tabs button').forEach(x=>x.classList.toggle('on',x===b));
 $$('.tab').forEach(t=>t.classList.toggle('on',t.id===b.dataset.tab));try{localStorage.setItem('yuedu-tab',b.dataset.tab)}catch(e){}});
try{const t=localStorage.getItem('yuedu-tab');const b=t&&$(`.tabs button[data-tab="${t}"]`);if(b)b.click()}catch(e){}
$('#q').oninput=e=>{const q=e.target.value.trim().toLowerCase();$$('article').forEach(a=>a.classList.toggle('hide',!!q&&!a.dataset.s.includes(q)&&!a.textContent.toLowerCase().includes(q)))};
// 「原视频」：弹窗播放本地视频 / 查看本地图文图片
const pl=$('#pl'),stage=pl.querySelector('.stage'),info=pl.querySelector('.info');
let list=[],cur=-1,timer=0;
function load(a,onEnd){clearTimeout(timer);stage.innerHTML='';
 if(a.dataset.video){const v=document.createElement('video');v.controls=true;v.autoplay=true;v.src=a.dataset.video;
  v.onerror=()=>{stage.innerHTML='<p style="color:#fff;padding:30px">打不开这个本地视频，可能已被移动或删除。</p>';if(onEnd)timer=setTimeout(onEnd,1200)};
  if(onEnd)v.onended=onEnd;stage.append(v)}
 else{const box=document.createElement('div');box.className='imgs';const srcs=JSON.parse(a.dataset.imgs);srcs.forEach(s=>{const i=new Image();i.src=s;box.append(i)});stage.append(box);
  if(onEnd){// 连播里的图文：按内容长短停 5～15 秒（每张约 5 秒、或按约 100px/秒滚完），期间自动往下滚，最多 15 秒就跳下一条
   const t0=performance.now();
   const step=now=>{if(!box.isConnected)return;const max=box.scrollHeight-box.clientHeight;
    const ms=Math.min(15000,Math.max(5000,srcs.length*5000,max*10+2000)),p=Math.min(1,(now-t0)/ms);box.scrollTop=max*p;
    if(p<1)requestAnimationFrame(step);else onEnd()};requestAnimationFrame(step)}}}
document.addEventListener('click',e=>{const a=e.target.closest('a.play');if(!a)return;e.preventDefault();
 list=[];pl.classList.remove('list');load(a);if(!pl.open)pl.showModal()});
// 连播 / 洗牌连播：按钮所在的整个 tab、大类（到下一个大类为止）或小主题（到下一个标题为止），跳过被搜索隐藏的
function scope(b){const h=b.closest('h2,h3,.tabplay');let r=[];
 if(h.classList.contains('tabplay'))r=[...h.parentElement.querySelectorAll('article')];
 else{const stop=h.tagName==='H2'?['H2']:['H2','H3'];for(let n=h.nextElementSibling;n&&!stop.includes(n.tagName);n=n.nextElementSibling)if(n.tagName==='ARTICLE')r.push(n)}
 const img=withImg();return r.filter(a=>!a.classList.contains('hide')&&!a.classList.contains('gone')&&a.querySelector(img?'a.play':'a.play[data-video]'))}
// 「连播带图文」勾选：所有 tab 共用一个设置，记在本机
function withImg(){const c=$('.withimg');return !c||c.checked}
try{const s=localStorage.getItem('yuedu-withimg');if(s!==null)$$('.withimg').forEach(c=>c.checked=s==='1')}catch(e){}
document.addEventListener('change',e=>{if(!e.target.classList.contains('withimg'))return;const v=e.target.checked;
 $$('.withimg').forEach(c=>c.checked=v);try{localStorage.setItem('yuedu-withimg',v?'1':'0')}catch(e){}});
function go(i){if(!list.length)return;if(i>=list.length){pl.close();return}cur=Math.max(0,i);const art=list[cur];
 $$('article.now').forEach(x=>x.classList.remove('now'));art.classList.add('now');
 info.textContent=`${cur+1} / ${list.length} · ${art.querySelector('h5').textContent}`;load(art.querySelector('a.play'),()=>go(cur+1))}
document.addEventListener('click',e=>{const b=e.target.closest('button.pa');if(!b)return;e.preventDefault();e.stopPropagation();
 list=scope(b);if(!list.length){alert('这里没有可播放的原视频');return}
 if(b.dataset.mode==='shuf')for(let i=list.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[list[i],list[j]]=[list[j],list[i]]}
 pl.classList.add('list');if(!pl.open)pl.showModal();go(0)});
pl.querySelector('.next').onclick=()=>go(cur+1);pl.querySelector('.prev').onclick=()=>go(cur-1);
pl.addEventListener('keydown',e=>{if(!list.length)return;
 if(e.key==='ArrowDown'||e.key==='PageDown'){e.preventDefault();go(cur+1)}else if(e.key==='ArrowUp'||e.key==='PageUp'){e.preventDefault();go(cur-1)}});
let wt=0;pl.addEventListener('wheel',e=>{if(!list.length||e.target.closest('.imgs'))return;e.preventDefault();const t=Date.now();
 if(t-wt<700||Math.abs(e.deltaY)<30)return;wt=t;go(cur+(e.deltaY>0?1:-1))},{passive:false});
// 回收站：删除 = 在原处隐藏 + 回收站里加一行；恢复 = 反过来。记录存 localStorage（按页面路径分开）+ 生成时的 PRE_DEL
const DKEY='yuedu-del:'+location.pathname;let DEL=new Set(PRE_DEL.map(String));
try{(JSON.parse(localStorage.getItem(DKEY)||'[]')).forEach(i=>DEL.add(String(i)))}catch(e){}
const saveDel=()=>{try{localStorage.setItem(DKEY,JSON.stringify([...DEL]))}catch(e){}};
const bintab=$('#bintab');
function where(el){if(el.tagName==='ARTICLE'){const s=el.closest('section.tab'),b=$(`.tabs button[data-tab="${s.id}"]`);
 return '阅读 · '+(b?b.firstChild.textContent:'')}return '待处理'}
function binRow(el){const id=el.dataset.id;if(bintab.querySelector(`tr[data-id="${id}"]`))return;
 const title=el.tagName==='ARTICLE'?el.querySelector('h5').textContent:(el.children[2]||{}).textContent||'';
 const play=el.querySelector('a.play,a[target=_blank]');const tr=document.createElement('tr');tr.dataset.id=id;
 tr.innerHTML=`<td>#${id}</td><td>${where(el)}</td><td></td><td></td><td><button class=del data-restore=1>恢复</button></td>`;
 tr.children[2].textContent=title;if(play)tr.children[3].append(play.cloneNode(true));bintab.append(tr)}
function els(id){return $$(`article[data-id="${id}"],#tlow tr[data-id="${id}"],.side .tart a[data-id="${id}"]`)}
function refreshBin(){$('#bincnt').textContent=bintab.rows.length;$('#binempty').classList.toggle('gone',bintab.rows.length>0)}
function del(id){DEL.add(id);els(id).forEach(e=>{if(e.tagName!=='A')binRow(e);e.classList.add('gone')});saveDel();refreshBin()}
function restore(id){DEL.delete(id);els(id).forEach(e=>e.classList.remove('gone'));const r=bintab.querySelector(`tr[data-id="${id}"]`);if(r)r.remove();saveDel();refreshBin()}
DEL.forEach(id=>{if(els(id).length)del(id)});refreshBin();
document.addEventListener('click',e=>{const b=e.target.closest('button.del');if(!b||b.id==='binexp')return;e.preventDefault();
 const id=b.closest('[data-id]').dataset.id;b.dataset.restore?restore(id):del(id)});
$('#binexp').onclick=()=>{const blob=new Blob([JSON.stringify({page:PAGE_NAME,ids:[...DEL].map(Number)},null,1)],{type:'application/json'});
 const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`${PAGE_NAME}-回收站.json`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)};
// 左侧导航折叠：大类、小主题前的 ▸/▾ 单独折叠；顶部「全部折叠 / 全部展开」。默认只展开到小主题，状态存 localStorage
const FKEY='yuedu-fold:'+location.pathname;let FOLD={};try{FOLD=JSON.parse(localStorage.getItem(FKEY)||'{}')}catch(e){}
const saveFold=()=>{try{localStorage.setItem(FKEY,JSON.stringify(FOLD))}catch(e){}};
function fkey(el){const a=el.classList.contains('tc')?el.querySelector('a.tcat'):el;return a.getAttribute('href')}
function setFold(el,on){const c=el.classList.contains('tc')?'fold-c':'fold-t';el.classList.toggle(c,on);
 const b=el.classList.contains('tc')?el.querySelector(':scope>.fx'):el.previousElementSibling;if(b&&b.classList.contains('fx'))b.textContent=on?'▸':'▾';FOLD[fkey(el)]=on?1:0}
$$('.side .tc').forEach(tc=>{const b=document.createElement('button');b.className='fx';tc.prepend(b);b.onclick=e=>{e.preventDefault();setFold(tc,!tc.classList.contains('fold-c'));saveFold()};
 const k=fkey(tc);setFold(tc,k in FOLD?!!FOLD[k]:false)});
$$('.side a.tt').forEach(a=>{const b=document.createElement('button');b.className='fx';a.before(b);b.onclick=e=>{e.preventDefault();setFold(a,!a.classList.contains('fold-t'));saveFold()};
 const k=fkey(a);setFold(a,k in FOLD?!!FOLD[k]:true)});
$$('.side .side-in').forEach(s=>{const d=document.createElement('div');d.className='foldall';d.innerHTML='<button data-f=1>全部折叠</button><button data-f=0>全部展开</button>';s.prepend(d);
 d.onclick=e=>{const b=e.target.closest('button');if(!b)return;const on=b.dataset.f==='1';s.querySelectorAll('.tc,a.tt').forEach(x=>setFold(x,on));saveFold()}});
pl.querySelector('.x').onclick=()=>pl.close();
pl.addEventListener('click',e=>{if(e.target===pl)pl.close()});
pl.addEventListener('close',()=>{clearTimeout(timer);stage.innerHTML='';const n=$('article.now');
 if(list.length&&n){n.scrollIntoView({block:'center'});setTimeout(()=>n.classList.remove('now'),2500)}list=[];pl.classList.remove('list')});
</script>"""

