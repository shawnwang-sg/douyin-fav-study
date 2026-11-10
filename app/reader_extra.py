"""学习库阅读页的提速和阅读体验（在 reader_page / reader_topics 的基础上加，不改它们原样复制来的版面代码）：

- 文章按需排版（content-visibility）：几千篇也只排屏幕附近的，切 tab、搜索不卡
- 搜索：预建索引 + 防抖，空格隔开多个词；tab 上显示各自命中数，没命中的大类 / 小主题收起来
- 顶部信息条（一行文字和链接）：未读、本周新增、继续阅读、后台处理进度、搜索命中
- 已读 / 只看未读 / 只看本周新增
- 左侧目录跟着滚动高亮当前篇；切 tab 记住各自读到哪
- 键盘快捷键：j / k 上下篇、/ 搜索、s 精品、m 已读、p 原视频、u 只看未读、? 帮助
"""

CSS = r"""
section.tab article{content-visibility:auto;contain-intrinsic-size:auto 700px}
article{scroll-margin-top:calc(var(--tabs-h,64px) + 10px)}section.tab{scroll-margin-top:var(--tabs-h,64px)}
header{display:flex;flex-wrap:wrap;align-items:center;gap:8px 22px}header h1{flex:none}header #q{flex:1;min-width:220px;order:1}
#ibar{order:2;flex:0 0 100%;display:flex;flex-wrap:wrap;margin:0 0 4px;font-size:14px;line-height:1.7;
 font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
#ibar>span:empty{display:none}#ibar>span+span::before{content:"·";margin:0 10px;color:var(--mut)}
#ibar a{color:var(--mut);text-decoration:underline;text-decoration-style:dotted;text-underline-offset:4px}
#ibar a:hover,#ibar a.act{color:var(--acc)}#ibar a.act{font-weight:600;text-decoration-style:solid}
.tabs button.nohit{opacity:.4}
button.rd{border:1px solid var(--line);background:none;color:var(--mut);border-radius:999px;padding:0 9px;font:inherit;font-size:12px;
 line-height:1.7;cursor:pointer;flex:none;margin-right:8px;opacity:.55}
button.rd:hover{opacity:1;color:var(--acc);border-color:var(--acc)}
article.read button.rd{opacity:1;color:var(--acc);border-color:var(--acc)}
article.read h5{color:var(--mut)}
.side .tart a.read::after{content:" ✓";font-size:11px}
body.f-unread article.read:not(.keep),body.f-week article:not(.wk){display:none}
.side .ttop a.tt.cur,.side a.tcat.cur{color:var(--acc)}
#kbd{border:1px solid var(--line);border-radius:16px;background:var(--card);color:var(--fg);padding:18px 24px;width:min(420px,94vw);
 font:15px/1.9 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
#kbd::backdrop{background:rgba(0,0,0,.35)}#kbd h3{margin:0 0 6px;font-size:17px}#kbd table{font-size:14px;margin:0}
#kbd td{border:0;padding:2px 14px 2px 0}#kbd kbd{border:1px solid var(--line);border-radius:6px;padding:0 7px;background:var(--bg);font:13px/1.6 Consolas,monospace}
"""

# 信息条：服务器只放「共 N 篇」，其余由页面脚本填
def ibar(total, updated):
    return (f"<p class=sub id=ibar><span title='页面生成于 {updated}'>共 {total} 篇</span><span id=ib-unread></span>"
            "<span id=ib-week></span><span id=ib-resume></span><span id=ib-proc></span><span id=ib-q></span></p>")


JS = r"""<dialog id=kbd><h3>快捷键</h3><table>
<tr><td><kbd>j</kbd> <kbd>k</kbd></td><td>下一篇 / 上一篇</td></tr>
<tr><td><kbd>/</kbd></td><td>搜索（空格隔开多个词；Enter 跳到第一篇，Esc 清空）</td></tr>
<tr><td><kbd>s</kbd></td><td>精品（再按取消）</td></tr>
<tr><td><kbd>m</kbd></td><td>标为已读（再按取消）</td></tr>
<tr><td><kbd>p</kbd></td><td>播放这一篇的原视频 / 原图文</td></tr>
<tr><td><kbd>u</kbd></td><td>只看未读</td></tr>
<tr><td><kbd>?</kbd></td><td>打开这张表</td></tr></table></dialog>
<script>
(()=>{
const tabsBar=$('.tabs'),qEl=$('#q');
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const post=(u,b)=>fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
const ls={get(k){try{return localStorage.getItem(k)}catch(e){return null}},set(k,v){try{localStorage.setItem(k,v)}catch(e){}}};
const activeSec=()=>$('section.tab.on');
const isContent=s=>!!s&&/^t\d+$/.test(s.id);
const srcArts=()=>$$('article[id^="a"]');
const tabBtn=id=>$(`.tabs button[data-tab="${id}"]`);
const tabName=sec=>{const b=sec&&tabBtn(sec.id);return b?b.firstChild.textContent.trim():''};
const fitTabs=()=>document.documentElement.style.setProperty('--tabs-h',tabsBar.offsetHeight+'px');
fitTabs();addEventListener('resize',fitTabs);

// ---- 已读、本周新增
const READ=new Set((typeof PRE_READ!=='undefined'?PRE_READ:[]).map(Number));
const WEEK=new Set((typeof PRE_WEEK!=='undefined'?PRE_WEEK:[]).map(Number));
function rdBtn(a){let b=a.querySelector('button.rd');
 if(!b){const s=a.querySelector('.src');if(!s)return;b=document.createElement('button');b.className='rd';
  const d=s.querySelector('button.del');d?d.before(b):s.append(b)}
 const on=READ.has(+a.dataset.id);a.classList.toggle('read',on);b.textContent=on?'✓ 已读':'标为已读';
 b.title=on?'再点一下改回未读':'读完了点一下（快捷键 m）'}
$$('article[data-id]').forEach(a=>{rdBtn(a);if(WEEK.has(+a.dataset.id))a.classList.add('wk')});
$$('.side .tart a[data-id]').forEach(l=>{if(READ.has(+l.dataset.id))l.classList.add('read')});
async function setRead(id,on){
 try{const r=await post(`/api/items/${id}/action`,{action:'learned',value:on});if(!r.ok)throw 0}catch(e){alert('没保存上，稍后再试');return}
 on?READ.add(id):READ.delete(id);
 $$(`article[data-id="${id}"]`).forEach(a=>{if(on)a.classList.add('keep');rdBtn(a)});  // keep：只看未读时，刚标的这篇先不消失
 $$(`.side .tart a[data-id="${id}"]`).forEach(l=>l.classList.toggle('read',on));bar()}
document.addEventListener('click',e=>{const b=e.target.closest('button.rd');if(!b)return;e.preventDefault();
 const id=+b.closest('article').dataset.id;setRead(id,!READ.has(id))});

// ---- 只看未读 / 只看本周新增（未读会记住，本周新增不记）
let MODE=ls.get('yuedu-mode')==='unread'?'unread':'';
function paintMode(){document.body.classList.toggle('f-unread',MODE==='unread');document.body.classList.toggle('f-week',MODE==='week')}
function setMode(m){MODE=MODE===m?'':m;ls.set('yuedu-mode',MODE==='unread'?'unread':'');paintMode();
 $$('article.keep').forEach(a=>a.classList.remove('keep'));refresh();bar();scrollTo({top:Math.min(scrollY,$('header').offsetTop)})}
const visible=a=>{const c=a.classList;return !c.contains('hide')&&!c.contains('gone')
 &&!(MODE==='unread'&&c.contains('read')&&!c.contains('keep'))&&!(MODE==='week'&&!c.contains('wk'))};

// ---- 搜索：第一次用时建索引（标题 + 金句 + 作者 + 正文），之后每次只比字符串
let TXT=null,QT=[],HITS={total:0,per:{}};
function buildIdx(){if(TXT)return;TXT=new Map();
 for(const a of srcArts()){const b=a.querySelector('.body');TXT.set(a.dataset.id,(a.dataset.s||'')+'\n'+(b?b.textContent.toLowerCase():''))}}
function applyQ(v){QT=v.trim().toLowerCase().split(/\s+/).filter(Boolean);if(QT.length)buildIdx();
 for(const a of $$('article[data-id]')){let ok=true;
  if(QT.length){let t=TXT.get(a.dataset.id);if(t===undefined)t=a.textContent.toLowerCase();ok=QT.every(w=>t.includes(w))}
  a.classList.toggle('hide',!ok)}
 refresh();bar()}
// 筛选（搜索 / 只看未读 / 本周新增）之后：tab 上显示各自剩几篇，没有文章的大类、小主题和目录项收起来
function refresh(){const filtering=!!QT.length||!!MODE;let total=0;const per={};
 for(const sec of $$('section.tab')){if(!isContent(sec))continue;const c=sec.querySelector('.content');let n=0;const vis=new Set();
  if(c){let h2=null,h3=null,n2=0,n3=0;
   const end3=()=>{if(h3)h3.classList.toggle('hide',filtering&&!n3)},end2=()=>{if(h2)h2.classList.toggle('hide',filtering&&!n2)};
   for(const el of c.children){const t=el.tagName;
    if(t==='H2'){end3();end2();h2=el;h3=null;n2=n3=0}
    else if(t==='H3'){end3();h3=el;n3=0}
    else if(t==='ARTICLE'&&visible(el)){n++;n2++;n3++;vis.add(el.dataset.id)}}
   end3();end2();
   sec.querySelectorAll('.side .tc').forEach(tc=>{let nc=0;
    tc.querySelectorAll('.tart').forEach(ta=>{let k=0;
     for(const l of ta.children){const v=vis.has(l.dataset.id);l.classList.toggle('hide',filtering&&!v);if(v)k++}
     const tt=ta.previousElementSibling,fx=tt&&tt.previousElementSibling,off=filtering&&!k;
     ta.classList.toggle('hide',off);if(tt)tt.classList.toggle('hide',off);if(fx&&fx.classList.contains('fx'))fx.classList.toggle('hide',off);nc+=k});
    tc.classList.toggle('hide',filtering&&!nc)})}
  per[sec.id]=n;total+=n;
  const b=tabBtn(sec.id),sp=b&&b.querySelector('span');
  if(sp){if(sp.dataset.n===undefined)sp.dataset.n=sp.textContent;sp.textContent=filtering?n:sp.dataset.n;b.classList.toggle('nohit',filtering&&!n)}}
 HITS={total,per}}
let qt=0;qEl.oninput=e=>{clearTimeout(qt);const v=e.target.value;qt=setTimeout(()=>applyQ(v),v.trim()?130:0)};
function firstHit(){let sec=activeSec();
 if(!isContent(sec)||!HITS.per[sec.id]){const id=Object.keys(HITS.per).find(k=>HITS.per[k]);if(!id)return;tabBtn(id).click();sec=document.getElementById(id)}
 const a=[...sec.querySelectorAll('article[data-id]')].find(visible);if(a)goTo(a,false)}
qEl.addEventListener('keydown',e=>{
 if(e.key==='Enter'){e.preventDefault();clearTimeout(qt);applyQ(qEl.value);firstHit();qEl.blur()}
 else if(e.key==='Escape'){e.preventDefault();clearTimeout(qt);qEl.value='';applyQ('');qEl.blur()}});

// ---- 跳到某一篇（需要的话先切 tab）
function goTo(a,flash=true){const sec=a.closest('section.tab');
 if(sec&&!sec.classList.contains('on')){const b=tabBtn(sec.id);if(b)b.click()}
 const go=()=>a.scrollIntoView({block:'start'});go();requestAnimationFrame(()=>setTimeout(go,80));  // 上面的文章排好版后位置会变，再对一次
 if(flash){a.classList.add('now');setTimeout(()=>a.classList.remove('now'),2500)}}

// ---- 现在读到哪一篇：在正文那一栏、tab 条下面一点的位置取元素
function curArt(){const sec=activeSec();if(!sec)return null;const c=sec.querySelector('.content')||sec,r=c.getBoundingClientRect();
 const x=Math.min(innerWidth-30,r.left+Math.min(240,r.width/2)),y0=tabsBar.getBoundingClientRect().bottom+24;
 for(let y=y0;y<innerHeight;y+=70){const e=document.elementFromPoint(x,y),a=e&&e.closest('article[data-id]');if(a&&sec.contains(a))return a}
 return null}
// 左侧目录跟着走：当前篇、它的小主题、大类都点亮，并把目录滚到看得见
function follow(a){$$('.side .cur').forEach(x=>x.classList.remove('cur'));
 const sec=activeSec(),side=sec&&sec.querySelector('.side');if(!a||!side)return;
 const l=side.querySelector(`.tart a[data-id="${a.dataset.id}"]`);if(!l)return;
 const tt=l.parentElement.previousElementSibling,tc=l.closest('.tc'),tcat=tc&&tc.querySelector('a.tcat');
 [l,tt,tcat].forEach(e=>e&&e.classList.add('cur'));
 const tgt=[l,tt,tcat].find(e=>e&&e.offsetParent);if(!tgt||getComputedStyle(side).position!=='sticky')return;
 const sr=side.getBoundingClientRect(),r=tgt.getBoundingClientRect();
 if(r.top<sr.top+30||r.bottom>sr.bottom-30)side.scrollTop+=r.top-sr.top-sr.height/3}
// 继续阅读：记住最后读到的那一篇（记在本机浏览器里）
const PKEY='yuedu-pos';let cur=null,st=0,pt=0,moved=0,resumed=false;
function track(){const a=curArt();if(a===cur)return;cur=a;follow(a);
 if(!a||!isContent(activeSec()))return;moved++;
 if(resumed||moved>3){clearTimeout(pt);pt=setTimeout(()=>ls.set(PKEY,JSON.stringify({id:+a.dataset.id,t:Date.now()})),600)}}
addEventListener('scroll',()=>{if(st)return;st=setTimeout(()=>{st=0;track()},120)},{passive:true});
// 切 tab：记住每个 tab 各自读到哪，切回来回到原处；没去过的 tab 从头开始
const TOPS={};
tabsBar.addEventListener('click',e=>{if(!e.target.closest('button'))return;const s=activeSec(),a=curArt();
 if(s&&a)TOPS[s.id]={a,off:a.getBoundingClientRect().top}},true);
tabsBar.addEventListener('click',e=>{if(!e.target.closest('button'))return;const s=activeSec();if(!s)return;
 const p=TOPS[s.id];
 if(p&&p.a.isConnected&&s.contains(p.a)&&visible(p.a)){p.a.scrollIntoView({block:'start'});scrollBy(0,p.a.getBoundingClientRect().top-p.off)}
 else if(tabsBar.getBoundingClientRect().top<=1)s.scrollIntoView({block:'start'});
 cur=null;setTimeout(track,0)});

// ---- 顶部信息条：一行文字和链接
const ib={unread:$('#ib-unread'),week:$('#ib-week'),resume:$('#ib-resume'),proc:$('#ib-proc'),q:$('#ib-q')};
function bar(){if(!ib.unread)return;let u=0,w=0;
 for(const a of srcArts()){if(a.classList.contains('gone'))continue;const id=+a.dataset.id;if(!READ.has(id))u++;if(WEEK.has(id))w++}
 ib.unread.innerHTML=`<a href="#" data-mode=unread class="${MODE==='unread'?'act':''}" title="读完一篇点「标为已读」；点这里只看还没读的">`
  +(MODE==='unread'?`只看未读 ${u.toLocaleString()} ✕`:`未读 ${u.toLocaleString()}`)+'</a>';
 ib.week.innerHTML=(w||MODE==='week'?`<a href="#" data-mode=week class="${MODE==='week'?'act':''}" title="只看这一周新进学习库的">`
  +(MODE==='week'?`只看本周新增 ${w} ✕`:`本周新增 ${w}`)+'</a>　':'')+'<a href="/#/weekly">周报</a>';
 const sec=activeSec();
 ib.q.textContent=!QT.length?'':!HITS.total?'没有搜到':`搜到 ${HITS.total} 篇`+(isContent(sec)&&!HITS.per[sec.id]?'，这个分类里没有，按 Enter 跳过去':'')}
document.addEventListener('click',e=>{const a=e.target.closest('#ibar a[data-mode]');if(!a)return;e.preventDefault();setMode(a.dataset.mode)});
(()=>{let p=null;try{p=JSON.parse(ls.get(PKEY)||'null')}catch(e){}
 const a=p&&document.getElementById('a'+p.id);if(!a||a.classList.contains('gone')||!ib.resume){resumed=true;return}
 const t=((a.querySelector('h5')||{}).textContent||'').trim()||'（无题）';
 ib.resume.innerHTML='继续阅读：<a href="#"></a>';const l=ib.resume.querySelector('a');
 l.textContent=t.length>24?t.slice(0,24)+'…':t;l.title='上次读到这里 · '+tabName(a.closest('section.tab'));
 l.onclick=e=>{e.preventDefault();resumed=true;ib.resume.textContent='';goTo(a)}})();
// 后台处理进度（处理中 / 待润色 / 失败 / 有新文章进库），点了去「添加与处理」
let done0=null,ptimer=0;
async function poll(){clearTimeout(ptimer);if(document.hidden||!ib.proc)return;let s=null;
 try{s=await fetch('/api/state').then(r=>r.json())}catch(e){}
 let wait=20000;
 if(s){const w=s.worker||{},bs=(s.stats||{}).by_status||{},parts=[];if(done0===null)done0=bs.done||0;
  if(w.busy){wait=5000;const stage=String(w.stage||'').replace(/\s+/g,' ').slice(0,26);
   parts.push(`<a href="/#/process" class=act>处理中`+(w.total?` ${w.done||0}/${w.total}`:'')+(stage?'：'+esc(stage):'')+'</a>')}
  else{if(bs.need_key)parts.push(`<a href="/#/process">待润色 ${bs.need_key}</a>`);if(bs.failed)parts.push(`<a href="/#/process">失败 ${bs.failed}</a>`)}
  if((bs.done||0)>done0)parts.push(`<a href="#" onclick="location.reload();return false" class=act>新进库 ${(bs.done||0)-done0} 篇，点这里刷新</a>`);
  ib.proc.innerHTML=parts.join('　')}
 ptimer=setTimeout(poll,wait)}
document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll()});

// ---- 键盘
function step(d){const sec=activeSec();if(!sec)return;const list=[...sec.querySelectorAll('article[data-id]')].filter(visible);if(!list.length)return;
 const c=curArt(),i=list.indexOf(c),line=tabsBar.getBoundingClientRect().bottom;
 if(i<0){goTo(list[0],false);return}
 const top=c.getBoundingClientRect().top;
 if((d<0&&top<line-30)||(d>0&&top>line+60)){goTo(c,false);return}  // 先对齐当前这一篇的开头
 goTo(list[Math.max(0,Math.min(list.length-1,i+d))],false)}
const kbd=$('#kbd');kbd.addEventListener('click',e=>{if(e.target===kbd)kbd.close()});
document.addEventListener('keydown',e=>{if(e.ctrlKey||e.metaKey||e.altKey||e.isComposing)return;const t=e.target;
 if(t.matches&&t.matches('input,textarea,select,[contenteditable]'))return;if($('dialog[open]'))return;
 const c=()=>curArt(),hit=sel=>{const a=c(),b=a&&a.querySelector(sel);if(b)b.click()};
 switch(e.key){
  case 'j':step(1);break;case 'k':step(-1);break;
  case '/':qEl.focus();qEl.select();break;
  case 's':hit('button.star');break;
  case 'm':{const a=c();if(a){const id=+a.dataset.id;setRead(id,!READ.has(id))}break}
  case 'p':hit('a.play');break;
  case 'u':setMode('unread');break;
  case '?':kbd.showModal();break;
  default:return}
 e.preventDefault()});
const gt=$('.gotop');if(gt){const b=document.createElement('button');b.title='快捷键（按 ? 也行）';b.textContent='⌨ 快捷键';b.onclick=()=>kbd.showModal();gt.prepend(b)}

paintMode();if(MODE)refresh();bar();track();poll();
})();
</script>"""
