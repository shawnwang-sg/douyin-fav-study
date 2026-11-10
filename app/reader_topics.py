"""学习库里的「专题整理」tab：自己建分类（最多三级）→ 专题 → 从别的分类挑视频进来，集中看、连播、一键复制到桌面。

页面是 reader_build 生成的静态整页；这里只加一个 tab、一个选择条、一个选专题的弹窗和对应的 JS。
专题里的文章是从页面里已有的文章卡片复制过来的，所以播放、连播、搜索都和别的分类一样。
"""

# 特殊 tab 一共 5 个：精品、专题整理（放最前），待处理清单、回收站、质检（放最后），样式和内容分类区分开
STAR_BUTTON = "<button data-tab=tstar class=sptab>★ 精品<span id=stcnt></span></button>"
TAB_BUTTON = "<button data-tab=ttopic class=sptab>专题整理<span id=tpcnt></span></button>"

STAR_SECTION = ("<section id=tstar class=tab><div class=layout><aside class=side><div class=tp-side id=stnav></div></aside>"
                "<div class=content id=stview></div></div></section>")
SECTION = ("<section id=ttopic class=tab><div class=layout><aside class=side><div class=tp-side id=tptree></div></aside>"
           "<div class=content id=tpview></div></div></section>")

CSS = r"""
.tabs button.sptab{border-color:var(--acc);color:var(--acc);font-weight:600}
.tabs button.sptab.on{background:var(--acc);color:var(--onfg);border-color:var(--acc)}
.tabs button.sptab span{color:inherit;opacity:.75}
.hd button.star{border:0;background:none;padding:0 2px;font-size:22px;line-height:1;color:var(--mut);cursor:pointer;flex:none}
.hd button.star:hover{color:#d9a400}.hd button.star.on{color:#e0a800}
.st-nav a{display:block;color:var(--mut);text-decoration:none;font-size:14px;padding:1px 0 1px 12px}
.st-nav a:hover{color:var(--acc)}.st-nav a.c2{color:var(--fg);font-weight:600;padding-left:0;margin-top:8px}
.st-nav b{display:block;margin:14px 0 2px;color:var(--acc);font-size:15px}
#tpfeed{border:1px solid var(--line);border-radius:16px;background:var(--card);color:var(--fg);padding:20px 22px;width:min(480px,94vw);
 font:15px/1.8 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
#tpfeed::backdrop{background:rgba(0,0,0,.35)}#tpfeed h3{margin:0 0 6px;font-size:18px}
#tpfeed label{display:block;margin:10px 0 0}#tpfeed input[type=text],#tpfeed input[type=number]{font:inherit;padding:5px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)}
#tpfeed input[type=text]{width:100%}#tpfeed input[type=number]{width:90px}
#tpfeed .note{font-size:13px;margin:12px 0 0}
#tpfprog{margin-top:12px;border:1px solid var(--line);border-radius:10px;padding:8px 12px;background:var(--bg);font-size:13px;line-height:1.7;max-height:40vh;overflow:auto}
#tpfprog p{margin:4px 0}
button.purge{border:1px solid #b3261e;background:none;color:#b3261e;border-radius:999px;padding:0 9px;font:inherit;font-size:12px;line-height:1.7;cursor:pointer}
button.purge:hover{background:#b3261e;color:#fff}
mark.hl{background:#ffe680;color:inherit;font-size:inherit;border-radius:2px;padding:0 1px;cursor:pointer}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]) mark.hl{background:rgba(224,168,0,.38)}}
:root[data-theme=dark] mark.hl{background:rgba(224,168,0,.38)}
#selbar{position:fixed;z-index:30;display:flex;gap:2px;background:var(--on);border-radius:10px;padding:4px;box-shadow:0 6px 20px rgba(0,0,0,.25);
 font:13px/1.4 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
#selbar button{border:0;background:transparent;color:var(--onfg);border-radius:7px;padding:5px 10px;font:inherit;cursor:pointer;white-space:nowrap}
#selbar button:hover{background:rgba(255,255,255,.16)}
#nlpick{border:1px solid var(--line);border-radius:16px;background:var(--card);color:var(--fg);padding:18px 20px;width:min(480px,94vw);
 font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
#nlpick::backdrop{background:rgba(0,0,0,.35)}#nlpick h3{margin:0 0 4px;font-size:17px}
#nlpick blockquote{font-size:14px;max-height:5.4em;overflow:hidden;margin:8px 0 12px}
#nllist{display:flex;flex-direction:column;gap:6px;max-height:44vh;overflow:auto}
#nllist button{text-align:left;border:1px solid var(--line);background:var(--bg);color:var(--fg);border-radius:10px;padding:8px 12px;font:inherit;cursor:pointer}
#nllist button:hover{border-color:var(--acc);color:var(--acc)}
.tp-side{padding:4px 4px 30px 0}
.tp-head{display:flex;align-items:center;gap:8px;margin:2px 0 10px}.tp-head b{flex:1;font-size:16px}
.tp-side button,.tp-bt{border:1px solid var(--line);background:var(--card);color:var(--mut);border-radius:999px;padding:0 9px;
 font:inherit;font-size:12px;line-height:1.8;cursor:pointer}
.tp-side button:hover,.tp-bt:hover{color:var(--acc);border-color:var(--acc)}
.tpn{margin:2px 0 4px}.tpn .tpn{margin-left:14px;border-left:1px solid var(--line);padding-left:8px}
.tpn-row{display:flex;align-items:center;gap:6px;padding:3px 0}.tpn-name{flex:1;font-weight:600;color:var(--fg)}
.tpn.lv2>.tpn-row .tpn-name{font-weight:500}.tpn.lv3>.tpn-row .tpn-name{font-weight:400;color:var(--mut)}
.tpn-acts{display:none;gap:4px}.tpn-row:hover .tpn-acts{display:inline-flex}
@media (hover:none){.tpn-acts{display:inline-flex;flex-wrap:wrap}.tpn-row{flex-wrap:wrap}}
.tpt{display:block;margin:1px 0 1px 10px;padding:2px 8px;border-radius:8px;color:var(--fg);text-decoration:none;font-size:14px;cursor:pointer}
.tpt:hover{color:var(--acc)}.tpt.cur{background:var(--q);color:var(--acc)}.tpt span{color:var(--mut);font-size:12px;margin-left:6px}
.tpt::before{content:"▤ ";color:var(--mut)}
.tp-empty{color:var(--mut);font-size:15px;line-height:1.9}.tp-empty b{color:var(--fg)}
.tp-crumb{color:var(--mut);font-size:13px;margin-top:8px}
#tpview h2.tp-title{margin-top:4px}
#tpview .tabplay{margin:6px 0 10px;display:flex;flex-wrap:wrap;align-items:center;gap:8px}
#tpview .tabplay .pp{margin-left:0}
article.tp-missing{opacity:.7}
button.tpadd{border:1px solid var(--line);background:none;color:var(--mut);border-radius:999px;padding:0 9px;font:inherit;font-size:12px;line-height:1.7;cursor:pointer;flex:none;margin-left:auto;margin-right:8px}
button.tpadd:hover{color:var(--acc);border-color:var(--acc)}
article.tpsel{outline:2px solid var(--acc);outline-offset:3px}
article.tpsel button.tpadd{background:var(--acc);color:var(--onfg);border-color:var(--acc)}
#tpbar{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:9;display:flex;align-items:center;gap:10px;
 background:var(--on);color:var(--onfg);border-radius:999px;padding:8px 10px 8px 18px;box-shadow:0 6px 24px rgba(0,0,0,.25);
 font:14px/1.5 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
#tpbar button{border:0;border-radius:999px;padding:5px 14px;font:inherit;cursor:pointer}
#tpbar #tpgo{background:var(--acc);color:#fff}#tpbar #tpclr{background:transparent;color:inherit;opacity:.75}
#tpdlg{border:1px solid var(--line);border-radius:16px;background:var(--card);color:var(--fg);padding:20px 22px;width:min(560px,94vw);
 font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
#tpdlg::backdrop{background:rgba(0,0,0,.35)}
#tpdlg h3{margin:0 0 10px;font-size:18px}
#tppick{max-height:46vh;overflow:auto;display:flex;flex-direction:column;gap:6px;margin-bottom:14px}
#tppick button{text-align:left;border:1px solid var(--line);background:var(--bg);color:var(--fg);border-radius:10px;padding:8px 12px;font:inherit;cursor:pointer}
#tppick button:hover{border-color:var(--acc);color:var(--acc)}#tppick button small{color:var(--mut);margin-left:6px}
.tp-new{display:flex;flex-wrap:wrap;gap:8px;align-items:center;border-top:1px dashed var(--line);padding-top:12px}
.tp-new select,.tp-new input{font:inherit;padding:5px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)}
.tp-new input{flex:1;min-width:140px}
.tp-foot{display:flex;justify-content:flex-end;margin-top:12px}
.tp-wrap{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:28px;align-items:start}
.tp-note{position:sticky;top:64px;height:calc(100vh - 170px);display:flex;flex-direction:column;border:1px solid var(--line);
 border-radius:12px;background:var(--card);padding:12px 14px;margin-top:8px}
.tp-note-hd{display:flex;align-items:baseline;gap:10px;margin-bottom:8px;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
.tp-note-hd b{font-size:15px}.tp-note-hd span{color:var(--mut);font-size:12px;margin-left:auto}
#tpnote{flex:1;width:100%;resize:none;border:0;outline:0;background:transparent;color:var(--fg);font:16px/1.85 "Noto Serif SC","Source Han Serif SC","Songti SC","SimSun",serif}
#tpnote::placeholder{color:var(--mut)}
@media (max-width:1250px){.tp-wrap{grid-template-columns:1fr}.tp-note{position:static;height:auto;order:-1}#tpnote{min-height:160px;resize:vertical}}
#tptoast{position:fixed;left:50%;top:18px;transform:translateX(-50%);z-index:20;background:var(--on);color:var(--onfg);border-radius:10px;
 padding:8px 16px;font:14px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;max-width:90vw}
"""

HTML = """<div id=tpbar class=gone>已选 <b id=tpn>0</b> 条<button id=tpgo>加入专题…</button><button id=tpclr>取消选择</button></div>
<dialog id=tpdlg><h3>加入哪个专题？</h3><div id=tppick></div>
<div class=tp-new><span>新建专题：</span><select id=tpnode></select><input id=tpname placeholder=专题名><button class=tp-bt id=tpnew>新建并加入</button></div>
<div class=tp-foot><button class=tp-bt id=tpcancel>取消</button></div></dialog><div id=tptoast class=gone></div>
<div id=selbar class=gone></div>
<dialog id=nlpick><h3>加入哪个专题的笔记？</h3><blockquote id=nlq></blockquote><div id=nllist></div>
<div class=tp-foot><button class=tp-bt id=nlcancel>取消</button></div></dialog>
<dialog id=tpfeed><h3>代刷</h3><div class=note style="margin:0">用抖音、小红书自己的搜索搜下面的关键词，各拿 N 条学习库里还没有的视频，
放进这个专题，再自动下载 → 转文字 → 润色，收进学习库。一条一条来，防封号。</div>
<label>搜索关键词<input type=text id=tpfkw></label>
<label>每个平台 <input type=number id=tpfn min=1 max=30 value=5> 条</label>
<label><input type=checkbox class=tpfp value=douyin checked> 抖音</label><label style="margin-top:2px"><input type=checkbox class=tpfp value=xhs checked> 小红书</label>
<p class=note>点开始后这个框不关，每 5 秒刷新一次进度。弹出的浏览器窗口如果要验证或登录，在那里完成就行，会自动接着搜。</p>
<div id=tpfprog hidden></div>
<div class=tp-foot style="gap:8px"><button class=tp-bt id=tpfcancel>取消</button><button class=tp-bt id=tpfgo style="border-color:var(--acc);color:var(--acc)">开始代刷</button></div></dialog>"""

JS = r"""<script>
(()=>{
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
const TP={data:{nodes:[],topics:[]},cur:null,sel:new Set()};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let tt=0;function toast(m,ms=3000){const t=$('#tptoast');t.textContent=m;t.classList.remove('gone');clearTimeout(tt);tt=setTimeout(()=>t.classList.add('gone'),ms)}
async function post(b){const r=await fetch('/api/topics',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
 const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.error||'出错了');return j}
async function load(){try{TP.data=await fetch('/api/topics').then(r=>r.json())}catch(e){}
 tree();$('#tpcnt').textContent=TP.data.topics.length||'';if(TP.cur&&!topic(TP.cur))TP.cur=null;show(TP.cur)}
const kids=pid=>TP.data.nodes.filter(n=>(n.parent_id||null)===(pid||null));
const tops=nid=>TP.data.topics.filter(t=>t.node_id===nid);
const node=id=>TP.data.nodes.find(n=>n.id===id),topic=id=>TP.data.topics.find(t=>t.id===id);
function path(nid){const p=[];let n=node(nid);while(n){p.unshift(n.name);n=node(n.parent_id)}return p}
function nodeHtml(n,lv){
 return `<div class="tpn lv${lv}" data-node=${n.id}><div class=tpn-row><span class=tpn-name>${esc(n.name)}</span><span class=tpn-acts>`
  +(lv<(TP.data.max_depth||3)?`<button data-op=sub title="在这个分类下面建子分类">＋分类</button>`:'')
  +`<button data-op=newtopic title="在这个分类下建专题">＋专题</button><button data-op=ren>改名</button><button data-op=delnode>删</button></span></div>`
  +tops(n.id).map(t=>`<a class="tpt${t.id===TP.cur?' cur':''}" data-topic=${t.id}>${esc(t.name)}<span>${t.items.length}</span></a>`).join('')
  +kids(n.id).map(c=>nodeHtml(c,lv+1)).join('')+`</div>`}
function tree(){const roots=kids(null);
 $('#tptree').innerHTML=`<div class=tp-head><b>我的专题</b><button data-op=root>＋ 一级分类</button></div>`
  +(roots.length?roots.map(n=>nodeHtml(n,1)).join(''):`<p class=tp-empty>还没有分类。点上面「＋ 一级分类」开始。</p>`)}
// 配套笔记：右侧一栏，边写边自动保存（停手 0.8 秒、离开输入框、切专题、关页面时都会存）
let noteTimer=0,notePending=null;
async function saveNote(){clearTimeout(noteTimer);const p=notePending;if(!p)return;notePending=null;const st=$('#tpnst');
 try{const r=await post({op:'note',topic_id:p.id,text:p.text});const t=topic(p.id);if(t){t.note=p.text;t.note_at=r.saved_at}
  if(st&&TP.cur===p.id)st.textContent='已保存 '+new Date(r.saved_at*1000).toLocaleTimeString('zh-CN',{hour12:false,hour:'2-digit',minute:'2-digit'})}
 catch(e){notePending=notePending||p;if(st)st.textContent='没保存上：'+e.message}}
document.addEventListener('input',e=>{if(e.target.id!=='tpnote')return;const t=topic(TP.cur);if(!t)return;
 t.note=e.target.value;notePending={id:t.id,text:e.target.value};$('#tpnst').textContent='正在输入…';clearTimeout(noteTimer);noteTimer=setTimeout(saveNote,800)});
document.addEventListener('focusout',e=>{if(e.target.id==='tpnote')saveNote()});
window.addEventListener('beforeunload',()=>{if(notePending)fetch('/api/topics',{method:'POST',keepalive:true,headers:{'Content-Type':'application/json'},
 body:JSON.stringify({op:'note',topic_id:notePending.id,text:notePending.text})})});
function show(id){if(notePending)saveNote();TP.cur=id;$$('#tptree .tpt').forEach(a=>a.classList.toggle('cur',+a.dataset.topic===id));
 const v=$('#tpview'),t=id&&topic(id);
 if(!t){v.innerHTML=`<div class=tp-empty><h2>专题整理</h2><p>这里是集中学习的地方：自己建分类（最多三级），每个分类下面建专题，再把别的分类里的视频挑进来，一起看、连播。</p>
  <p><b>怎么用：</b>①左边「＋ 一级分类」建分类，鼠标移到分类上可以加子分类、建专题、改名、删除；②到别的分类里，点文章右上角的「＋专题」，可以连着选好几条；③底部出现选择条，点「加入专题…」选一个专题；④在专题里点「复制到桌面」，视频会按分类层级复制到桌面的「专题学习」文件夹，方便剪辑。</p></div>`;return}
 const saved=t.note_at?'上次保存 '+new Date(t.note_at*1000).toLocaleString('zh-CN',{hour12:false,month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}):'自动保存';
 v.innerHTML=`<div class=tp-wrap><div class=tp-main><div class=tp-crumb>${path(t.node_id).map(esc).join(' › ')}</div><h2 class=tp-title>${esc(t.name)}</h2>`
  +`<div class=tabplay>共 ${t.items.length} 条 ${PLAYBTN_HTML}<button class=tp-bt data-op=copy title="按分类层级复制到 桌面\\专题学习（笔记也会存成 笔记.md）">复制到桌面</button>`
  +`<button class=tp-bt data-op=feed title="用抖音、小红书搜索找新视频补进这个专题">代刷</button>`
  +`<button class=tp-bt data-op=tren>改名</button><button class=tp-bt data-op=tdel>删除专题</button></div></div>`
  +`<aside class=tp-note><div class=tp-note-hd><b>专题笔记</b><span id=tpnst>${saved}</span></div>`
  +`<textarea id=tpnote placeholder="随手记：要点、动作要领、哪条视频第几分钟值得反复看、剪辑思路……&#10;边写边自动保存。">${esc(t.note||'')}</textarea></aside></div>`;
 const m=v.querySelector('.tp-main');
 if(!t.items.length){m.insertAdjacentHTML('beforeend',`<p class=tp-empty>这个专题还是空的。到别的分类里点文章上的「＋专题」把视频加进来。</p>`);return}
 for(const it of t.items){const src=document.getElementById('a'+it.id);let a;
  if(src){a=src.cloneNode(true);a.id='tpa'+it.id;a.classList.remove('gone','hide','now','tpsel');
   a.querySelectorAll('button.tpadd').forEach(b=>b.remove());  // 「删除」留着：在专题里删也是进回收站，各处一起隐藏
   if(it.source==='search'){a.classList.add('tpsearch');a.querySelectorAll('button.del').forEach(b=>b.remove())}  // 代刷来的：本来就在回收站，专题里照常显示，用「移出专题」拿掉
   else if(isDel(it.id))a.classList.add('gone')}
  else{a=document.createElement('article');a.className='tp-missing';a.dataset.id=it.id;
   const why=it.missing?'这条已经从学习台删掉了。':it.status==='skipped'?'这条没进学习库正文（被跳过了），只能看原视频。'
    :it.status==='done'?'这条在学习库里被删到回收站了。':'这条还在处理中，整理好以后刷新页面就能看到正文。';
   a.innerHTML=`<div class=hd><h5>${esc(it.title||('#'+it.id))}</h5>`+(it.video?`<a class=play data-video="/media/${it.id}" href="/media/${it.id}">原视频</a>`:'')
    +`</div><div class=src><span>—— ${esc(it.author||'')}</span></div><p class=note>${why}</p>`}
  const rm=document.createElement('button');rm.className='tp-bt';rm.dataset.op='rm';rm.dataset.id=it.id;rm.textContent='移出专题';
  (a.querySelector('.src')||a).append(rm);m.append(a)}}
// 左边树和专题页上的按钮
document.addEventListener('click',async e=>{const b=e.target.closest('#ttopic [data-op],#ttopic .tpt');if(!b)return;e.preventDefault();
 if(b.classList.contains('tpt')){show(+b.dataset.topic);return}
 const op=b.dataset.op,nid=+(b.closest('[data-node]')||{dataset:{}}).dataset.node,t=topic(TP.cur);
 try{
  if(op==='root'||op==='sub'){const name=prompt(op==='root'?'一级分类的名字':'子分类的名字');if(!name)return;await post({op:'node_create',parent_id:op==='sub'?nid:null,name})}
  else if(op==='newtopic'){const name=prompt('专题的名字');if(!name)return;const r=await post({op:'topic_create',node_id:nid,name});TP.cur=r.id}
  else if(op==='ren'){const name=prompt('新名字',node(nid).name);if(!name)return;await post({op:'node_rename',id:nid,name})}
  else if(op==='delnode'){if(!confirm(`删除分类「${node(nid).name}」？下面的子分类和专题会一起删掉（视频本身不动）。`))return;await post({op:'node_delete',id:nid})}
  else if(op==='tren'){const name=prompt('专题的新名字',t.name);if(!name)return;await post({op:'topic_rename',id:t.id,name})}
  else if(op==='tdel'){if(!confirm(`删除专题「${t.name}」？（只删专题，视频本身不动）`))return;await post({op:'topic_delete',id:t.id});TP.cur=null}
  else if(op==='rm'){await post({op:'remove',topic_id:t.id,ids:[+b.dataset.id]})}
  else if(op==='feed'){$('#tpfkw').value=t.name;$('#tpfeed').showModal();return}
  else if(op==='copy'){b.disabled=true;b.textContent='复制中…';
   try{const r=await post({op:'copy',topic_id:t.id});
    toast(`已复制 ${r.copied} 个到 ${r.dir}`+(r.same?`（${r.same} 个之前复制过）`:'')+(r.missing.length?`；${r.missing.length} 个找不到本地文件`:''),7000)}
   finally{b.disabled=false;b.textContent='复制到桌面'}return}
  await load()}catch(err){alert(err.message)}});
// 别的分类里：每篇文章加「＋专题」，可以多选
$$('section.tab:not(#ttopic) article').forEach(a=>{const s=a.querySelector('.src');if(!s)return;
 const b=document.createElement('button');b.className='tpadd';b.title='加入专题整理（可以连着选好几条）';b.textContent='＋专题';
 const d=s.querySelector('button.del');d?s.insertBefore(b,d):s.append(b)});
function bar(){$('#tpn').textContent=TP.sel.size;$('#tpbar').classList.toggle('gone',!TP.sel.size)}
document.addEventListener('click',e=>{const b=e.target.closest('button.tpadd');if(!b)return;e.preventDefault();
 const a=b.closest('article'),id=+a.dataset.id;if(TP.sel.has(id)){TP.sel.delete(id);a.classList.remove('tpsel');b.textContent='＋专题'}
 else{TP.sel.add(id);a.classList.add('tpsel');b.textContent='✓ 已选'}bar()});
function clearSel(){TP.sel.clear();$$('article.tpsel').forEach(a=>{a.classList.remove('tpsel');const b=a.querySelector('button.tpadd');if(b)b.textContent='＋专题'});bar()}
$('#tpclr').onclick=clearSel;
const dlg=$('#tpdlg');
function nodeOpts(pid,lv){return kids(pid).map(n=>`<option value=${n.id}>${'　'.repeat(lv)}${esc(n.name)}</option>`+nodeOpts(n.id,lv+1)).join('')}
$('#tpgo').onclick=()=>{const ts=TP.data.topics;
 $('#tppick').innerHTML=ts.length?ts.map(t=>`<button data-t=${t.id}>${esc(path(t.node_id).concat(t.name).join(' › '))}<small>${t.items.length} 条</small></button>`).join('')
  :`<p class=tp-empty>还没有专题。可以在下面直接新建一个。</p>`;
 const opts=nodeOpts(null,0);$('#tpnode').innerHTML=opts||'<option value="">（先新建一个一级分类）</option>';
 $('#tpnode').insertAdjacentHTML('beforeend','<option value=new>＋ 新建一级分类…</option>');
 $('#tpname').value='';dlg.showModal()};
async function addTo(tid){const ids=[...TP.sel];const r=await post({op:'add',topic_id:tid,ids});dlg.close();clearSel();await load();
 const t=topic(tid);toast(`已加入「${t?t.name:''}」：新增 ${r.added} 条`+(ids.length>r.added?`（${ids.length-r.added} 条本来就在里面）`:''))}
$('#tppick').onclick=e=>{const b=e.target.closest('button[data-t]');if(b)addTo(+b.dataset.t).catch(err=>alert(err.message))};
$('#tpnew').onclick=async()=>{try{let nid=$('#tpnode').value;const name=$('#tpname').value.trim();if(!name){$('#tpname').focus();return}
 if(nid==='new'||!nid){const nn=prompt('一级分类的名字');if(!nn)return;nid=(await post({op:'node_create',parent_id:null,name:nn})).id}
 const r=await post({op:'topic_create',node_id:+nid,name});await load();await addTo(r.id)}catch(err){alert(err.message)}};
$('#tpcancel').onclick=()=>dlg.close();
// ---- 代刷
$('#tpfcancel').onclick=()=>$('#tpfeed').close();
$('#tpfgo').onclick=async()=>{const t=topic(TP.cur);if(!t)return;const kw=$('#tpfkw').value.trim()||t.name;
 const n=Math.max(1,Math.min(30,+$('#tpfn').value||5)),platforms=$$('.tpfp').filter(c=>c.checked).map(c=>c.value);
 if(!platforms.length){alert('至少选一个平台');return}
 try{const t0=Date.now()/1000-2;await post({op:'feed',topic_id:t.id,keyword:kw,n,platforms});feedWatch(kw,t0)}catch(e){alert(e.message)}};
// 代刷开始后：对话框不关，每 5 秒看一次进度（要验证 / 登录时会提示你去弹出的窗口里完成，完成后自动接着搜）
let feedTimer=0;
function feedWatch(kw,t0){const box=$('#tpfprog');box.hidden=false;$('#tpfgo').hidden=true;$('#tpfcancel').textContent='关闭（后台继续）';
 const keys=['代刷','抖音：','小红书：','放进专题','验证','登录','搜索没完成'];
 const tick=async()=>{let s;try{s=await fetch('/api/state').then(r=>r.json())}catch(e){feedTimer=setTimeout(tick,5000);return}
  const L=(s.logs||[]).filter(l=>l.t>=t0&&keys.some(k=>l.msg.includes(k)));
  const w=s.worker||{},queued=!L.length&&w.busy;
  box.innerHTML=(queued?`<p>后台正在处理「${esc((w.stage||'').slice(0,24))}」，做完这一条就去搜……</p>`:'')
   +L.slice(-8).map(l=>`<div>${new Date(l.t*1000).toLocaleTimeString('zh-CN',{hour12:false})} ${esc(l.msg)}</div>`).join('');
  const done=L.find(l=>l.msg.includes('搜完了')||(l.msg.includes(`代刷「${kw}」`)&&l.msg.includes('完成')));
  if(done){box.insertAdjacentHTML('beforeend','<p><b>搜完了。</b>新视频在后台下载、转文字、润色，好了以后刷新学习库就能在专题里看到。</p>');
   $('#tpfcancel').textContent='关闭';load();return}
  feedTimer=setTimeout(tick,5000)};
 tick()}
$('#tpfeed').addEventListener('close',()=>{clearTimeout(feedTimer);$('#tpfprog').hidden=true;$('#tpfprog').innerHTML='';$('#tpfgo').hidden=false;$('#tpfcancel').textContent='取消'});
// ---- 删除同步：学习库里删除（进回收站）的，专题、精品里一起隐藏；恢复时一起回来（用的是同一套 data-id）
function isDel(id){try{return DEL.has(String(id))}catch(e){return false}}
// ---- 精品：每篇文章标题前一颗星，点亮 = 精品，复制一份到「精品」tab，按原来的 分类 → 大类 → 小主题 排目录
const STAR=new Set((typeof PRE_STAR!=='undefined'?PRE_STAR:[]).map(Number));
const CONTENT='section.tab:not(#ttopic):not(#tstar):not(#tlow):not(#tbin):not(#tqc) article';
function starBtn(id){const b=document.createElement('button');b.className='star'+(STAR.has(id)?' on':'');
 b.title='精品：点亮后会复制一份到「精品」tab（再点一下取消）';b.textContent=STAR.has(id)?'★':'☆';return b}
$$(CONTENT).forEach(a=>{const h=a.querySelector('.hd');if(h)h.prepend(starBtn(+a.dataset.id))});
function starCount(){$('#stcnt').textContent=STAR.size||''}
document.addEventListener('click',async e=>{const b=e.target.closest('button.star');if(!b)return;e.preventDefault();e.stopPropagation();
 const id=+b.closest('article').dataset.id,on=!STAR.has(id);
 try{const r=await fetch(`/api/items/${id}/action`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'star',value:on})});
  if(!r.ok)throw new Error('没保存上')}catch(err){alert(err.message);return}
 on?STAR.add(id):STAR.delete(id);
 $$(`article[data-id="${id}"] button.star`).forEach(x=>{x.classList.toggle('on',on);x.textContent=on?'★':'☆'});
 starCount();toast(on?'已加入精品':'已从精品里拿掉',1600);if($('#tstar').classList.contains('on'))buildStar()});
function buildStar(){const nav=$('#stnav'),v=$('#stview'),g=new Map();let total=0;
 for(const a of $$(CONTENT)){const id=+a.dataset.id;if(!STAR.has(id))continue;
  const sec=a.closest('section.tab'),tb=$(`.tabs button[data-tab="${sec.id}"]`),tab=tb?tb.firstChild.textContent.trim():'';
  let h2=null,h3=null;for(let n=a.previousElementSibling;n;n=n.previousElementSibling){if(!h3&&n.tagName==='H3')h3=n;if(n.tagName==='H2'){h2=n;break}}
  const c2=h2?h2.firstChild.textContent.trim():'其他',c3=h3?h3.firstChild.textContent.trim():'';
  if(!g.has(tab))g.set(tab,new Map());const gt=g.get(tab);if(!gt.has(c2))gt.set(c2,new Map());const gc=gt.get(c2);
  if(!gc.has(c3))gc.set(c3,[]);gc.get(c3).push(a);total++}
 if(!total){nav.innerHTML='';v.innerHTML=`<div class=tp-empty><h2>精品</h2><p>在任意分类里，点文章标题前面的 ☆，它就变成 ★ 精品，会复制一份到这里，按原来的分类排好目录。再点一下取消。</p></div>`;return}
 let navH='<div class="st-nav">',body=`<div class=tabplay>精品 共 ${total} 条 ${PLAYBTN_HTML}</div>`,k=0;
 for(const [tab,gt] of g){navH+=`<b>${esc(tab)}</b>`;
  for(const [c2,gc] of gt){const id2='st'+(k++);navH+=`<a class=c2 href="#${id2}">${esc(c2)}</a>`;body+=`<h2 id="${id2}">${esc(tab)} · ${esc(c2)}${PLAYBTN_HTML}</h2>`;
   for(const [c3,list] of gc){const id3='st'+(k++);if(c3){navH+=`<a href="#${id3}">${esc(c3)} <span>${list.length}</span></a>`;body+=`<h3 id="${id3}">${esc(c3)}${PLAYBTN_HTML}</h3>`}
    body+=list.map(a=>`<i data-clone="${a.dataset.id}"></i>`).join('')}}}
 nav.innerHTML=navH+'</div>';v.innerHTML=body;
 v.querySelectorAll('i[data-clone]').forEach(ph=>{const src=document.getElementById('a'+ph.dataset.clone),c=src.cloneNode(true);
  c.id='sta'+ph.dataset.clone;c.classList.remove('hide','now','tpsel','gone');c.querySelectorAll('button.tpadd').forEach(x=>x.remove());
  if(isDel(ph.dataset.clone))c.classList.add('gone');ph.replaceWith(c)})}
document.addEventListener('click',e=>{if(e.target.closest('.tabs button[data-tab=tstar]'))buildStar()});
// ---- 选中文字：高亮 / 复制 / 加入专题笔记；点已有的高亮可以取消
const HL=(typeof PRE_HL!=='undefined'?PRE_HL:{});
function textNodes(root){const w=document.createTreeWalker(root,NodeFilter.SHOW_TEXT),out=[];let n;
 while((n=w.nextNode()))if(!n.parentElement.closest('button,.pp,.src,a.play'))out.push(n);return out}
// 在文章里找到这句话（不管中间的空白、换行、分段），把它包成高亮；跨段落的会分几段包
function markText(root,quote){const q=quote.replace(/\s+/g,'');if(!q)return false;let flat='';const map=[];
 for(const n of textNodes(root)){const t=n.nodeValue;for(let i=0;i<t.length;i++){if(/\s/.test(t[i]))continue;flat+=t[i];map.push([n,i])}}
 const at=flat.indexOf(q);if(at<0)return false;const segs=new Map();
 for(let k=at;k<at+q.length;k++){const [n,i]=map[k];if(!segs.has(n))segs.set(n,[i,i]);else segs.get(n)[1]=i}
 for(const [n,[s,e]] of segs){const r=document.createRange();r.setStart(n,s);r.setEnd(n,e+1);
  const m=document.createElement('mark');m.className='hl';m.dataset.q=quote;try{r.surroundContents(m)}catch(err){}}
 return true}
function unmark(root,quote){root.querySelectorAll('mark.hl').forEach(m=>{if(m.dataset.q!==quote)return;
 const p=m.parentNode;while(m.firstChild)p.insertBefore(m.firstChild,m);p.removeChild(m);p.normalize()})}
const arts=id=>$$(`article[data-id="${id}"]`);
for(const [id,list] of Object.entries(HL))for(const a of arts(id))for(const q of list)markText(a,q);
const bar2=$('#selbar');let selInfo=null;
function hideBar(){bar2.classList.add('gone');selInfo=null}
function placeBar(rect){bar2.classList.remove('gone');const w=bar2.offsetWidth,h=bar2.offsetHeight;
 let x=rect.left+rect.width/2-w/2,y=rect.top-h-8;if(y<8)y=rect.bottom+8;x=Math.max(8,Math.min(innerWidth-w-8,x));
 bar2.style.left=x+'px';bar2.style.top=y+'px'}
function artInfo(a){return{id:+a.dataset.id,title:(a.querySelector('h5')||{}).textContent||'',
 author:((a.querySelector('.src span')||{}).firstChild||{}).textContent||''}}
document.addEventListener('mouseup',e=>{if(e.target.closest('#selbar,dialog'))return;setTimeout(()=>{
 const sel=getSelection(),txt=sel.toString().trim();
 if(!txt||sel.isCollapsed){if(!e.target.closest('mark.hl'))hideBar();return}
 const a=(sel.anchorNode&&sel.anchorNode.parentElement||{}).closest?.('article[data-id]');if(!a||!a.contains(sel.focusNode)){hideBar();return}
 selInfo={...artInfo(a),text:txt,mode:'sel'};
 bar2.innerHTML='<button data-s=all title="复制 + 高亮 + 加入专题笔记">一键三连</button><button data-s=hl>高亮</button><button data-s=copy>复制</button><button data-s=note>加入专题笔记</button>';
 placeBar(sel.getRangeAt(0).getBoundingClientRect())},10)});
document.addEventListener('click',e=>{const m=e.target.closest('mark.hl');if(!m||!getSelection().isCollapsed)return;
 const a=m.closest('article[data-id]');selInfo={...artInfo(a),text:m.dataset.q,mode:'mark'};
 bar2.innerHTML='<button data-s=all title="复制 + 加入专题笔记（已经高亮了）">一键三连</button><button data-s=unhl>取消高亮</button><button data-s=copy>复制</button><button data-s=note>加入专题笔记</button>';
 placeBar(m.getBoundingClientRect())});
document.addEventListener('scroll',hideBar,{passive:true});
async function copyText(t){try{await navigator.clipboard.writeText(t)}catch(e){const ta=document.createElement('textarea');ta.value=t;
 document.body.append(ta);ta.select();document.execCommand('copy');ta.remove()}}
// 摘抄格式：只放摘的那段话，不写出处标题；来自哪篇文章由后台记着（note_quotes 表）
async function addToNote(tid,info,prefix=''){const t=topic(tid);if(!t)return;
 const quote='> '+info.text.trim().replace(/\n+/g,'\n> ');
 const cur=(t.note||'').replace(/\s*$/,'');
 const text=cur+(cur?'\n\n':'')+quote;
 const r=await post({op:'note',topic_id:tid,text,quote:{item_id:info.id,text:info.text.trim()}});t.note=text;t.note_at=r.saved_at;
 if(TP.cur===tid&&$('#tpnote')){$('#tpnote').value=text;$('#tpnst').textContent='已保存 '+new Date(r.saved_at*1000).toLocaleTimeString('zh-CN',{hour12:false,hour:'2-digit',minute:'2-digit'})}
 toast(`${prefix}已加入「${t.name}」的笔记`)}
let pendingNote=null;
bar2.addEventListener('mousedown',e=>e.preventDefault());  // 点按钮时别把选区弄丢
bar2.addEventListener('click',async e=>{const b=e.target.closest('button');if(!b||!selInfo)return;const info=selInfo,s=b.dataset.s;hideBar();
 try{
  if(s==='copy'){await copyText(info.text);toast('已复制',1400)}
  else if(s==='hl')await doHl(info)
  else if(s==='unhl'){await fetch('/api/highlights',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({op:'remove',item_id:info.id,text:info.text})});
   HL[info.id]=(HL[info.id]||[]).filter(x=>x!==info.text);for(const a of arts(info.id))unmark(a,info.text)}
  else if(s==='note')await doNote(info)
  else if(s==='all'){  // 一键三连：复制 + 高亮 + 加入专题笔记（不在专题里就弹框选专题）
   await copyText(info.text);await doHl(info);
   if(!await doNote(info,'已复制、高亮，'))toast('已复制、高亮。选一个专题，把这段放进笔记',3000)}
 }catch(err){alert(err.message)}});
async function doHl(info){if((HL[info.id]||[]).includes(info.text))return;  // 已经高亮过的不重复
 await fetch('/api/highlights',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({op:'add',item_id:info.id,text:info.text})});
 (HL[info.id]=HL[info.id]||[]).push(info.text);for(const a of arts(info.id))markText(a,info.text);getSelection().removeAllRanges()}
// 加进专题笔记：正在看某个专题就直接加进它；否则弹框选专题（返回 false 表示等用户选）
async function doNote(info,prefix=''){const inTopic=$('#ttopic').classList.contains('on')&&TP.cur;
 if(inTopic){await addToNote(TP.cur,info,prefix);return true}
 if(!TP.data.topics.length){alert('还没有专题。先到「专题整理」里建一个分类和专题。');return true}
 pendingNote=info;$('#nlq').textContent=info.text;
 $('#nllist').innerHTML=TP.data.topics.map(t=>`<button data-t=${t.id}>${esc(path(t.node_id).concat(t.name).join(' › '))}</button>`).join('');
 $('#nlpick').showModal();return false}
$('#nllist').onclick=async e=>{const b=e.target.closest('button[data-t]');if(!b||!pendingNote)return;$('#nlpick').close();
 try{await addToNote(+b.dataset.t,pendingNote)}catch(err){alert(err.message)}pendingNote=null};
$('#nlcancel').onclick=()=>{$('#nlpick').close();pendingNote=null};
// ---- 回收站「彻底删除」：删掉记录和笔记（视频文件不动），元信息留底，以后代刷不会再刷到（同步收藏、手动添加仍可加回）
async function purge(ids){const r=await fetch('/api/library/purge',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids})});
 if(!r.ok)throw new Error('没删成');
 for(const id of ids){els(String(id)).forEach(e=>e.remove());const tr=$(`#bintab tr[data-id="${id}"]`);if(tr)tr.remove();DEL.delete(String(id));STAR.delete(+id)}
 saveDel();refreshBin();starCount();await load();return ids.length}
document.addEventListener('click',async e=>{const b=e.target.closest('button.purge');if(!b)return;e.preventDefault();
 const ids=b.id==='binpurge'?$$('#bintab tr[data-id]').map(tr=>+tr.dataset.id):[+b.closest('tr').dataset.id];
 if(!ids.length){toast('回收站是空的');return}
 if(!confirm(`彻底删除 ${ids.length} 条？\n删掉学习台里的记录、笔记、高亮和专题里的位置，不能恢复；本地视频文件不动。\n以后代刷不会再刷到它们；同步收藏、手动添加时仍可以加回来。`))return;
 try{toast(`已彻底删除 ${await purge(ids)} 条`)}catch(err){alert(err.message)}});
// 回收站按原来的分类排好（分类 › 大类 › 小主题，代刷来的排在一起），恢复就回到原来的分类
function sortBin(){const tb=$('#bintab');if(!tb)return;const rows=[...tb.querySelectorAll('tr[data-id]')];
 rows.sort((x,y)=>(x.children[1].textContent).localeCompare(y.children[1].textContent,'zh')||(+x.dataset.id-+y.dataset.id));
 rows.forEach(r=>tb.append(r))}
sortBin();
starCount();if($('#tstar').classList.contains('on'))buildStar();
load();
})();
</script>"""
