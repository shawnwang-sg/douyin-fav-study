"""专题整理：自己建的「分类（最多三级）→ 专题 → 视频」，集中学习；可以一键把专题里的视频复制到桌面「专题学习」文件夹。

数据存在 library.db 的三张表里：topic_nodes（分类树）、topics（专题）、topic_items（专题里的条目和顺序）。
"""
import os
import shutil
import time
from pathlib import Path

from . import db, notes

MAX_DEPTH = 3
ROOT_DIR_NAME = "专题学习"


def init():
    c = db.conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS topic_nodes(id INTEGER PRIMARY KEY, parent_id INTEGER, name TEXT NOT NULL,
        sort INTEGER DEFAULT 0, created_at REAL);
    CREATE TABLE IF NOT EXISTS topics(id INTEGER PRIMARY KEY, node_id INTEGER NOT NULL, name TEXT NOT NULL,
        sort INTEGER DEFAULT 0, created_at REAL);
    CREATE TABLE IF NOT EXISTS topic_items(topic_id INTEGER NOT NULL, item_id INTEGER NOT NULL, sort INTEGER DEFAULT 0,
        added_at REAL, PRIMARY KEY(topic_id, item_id));
    """)
    c.execute("CREATE TABLE IF NOT EXISTS highlights(id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL, text TEXT NOT NULL, "
              "created_at REAL)")  # 学习库里划线高亮的句子
    # 专题笔记里的摘抄是从哪篇文章来的：笔记正文里不写出处，来源记在这张表里
    c.execute("CREATE TABLE IF NOT EXISTS note_quotes(id INTEGER PRIMARY KEY, topic_id INTEGER NOT NULL, item_id INTEGER NOT NULL, "
              "text TEXT NOT NULL, created_at REAL)")
    cols = {r["name"] for r in c.execute("PRAGMA table_info(topics)")}
    if "note" not in cols:  # 每个专题的配套笔记（随手记）
        c.execute("ALTER TABLE topics ADD COLUMN note TEXT DEFAULT ''")
        c.execute("ALTER TABLE topics ADD COLUMN note_at REAL")
    c.commit()


class TopicError(Exception):
    pass


def _name(s):
    s = str(s or "").strip()
    if not s:
        raise TopicError("名字不能为空")
    return s[:60]


def _depth(node_id):
    d, c = 0, db.conn()
    while node_id:
        r = c.execute("SELECT parent_id FROM topic_nodes WHERE id=?", (node_id,)).fetchone()
        if not r:
            raise TopicError("找不到这个分类")
        d += 1
        node_id = r["parent_id"]
    return d


def all_data():
    c = db.conn()
    nodes = [dict(r) for r in c.execute("SELECT id,parent_id,name,sort FROM topic_nodes ORDER BY sort,id")]
    topics = [dict(r) for r in c.execute("SELECT id,node_id,name,sort,COALESCE(note,'') note,note_at FROM topics ORDER BY sort,id")]
    items = {}
    for r in c.execute("SELECT ti.topic_id, ti.item_id, i.title, i.author, i.platform, i.status, i.local_path, i.media_type, i.source "
                       "FROM topic_items ti LEFT JOIN items i ON i.id=ti.item_id ORDER BY ti.topic_id, ti.sort, ti.added_at"):
        lp = r["local_path"]
        items.setdefault(r["topic_id"], []).append(
            {"id": r["item_id"], "title": r["title"] or "", "author": r["author"] or "",
             "platform": r["platform"] or "", "missing": r["status"] is None, "status": r["status"] or "",
             "source": r["source"] or "",  # search = 代刷来的：在回收站里，但专题里照常显示
             # 不在学习库正文里的（跳过的 / 还在处理的）：本地有视频文件就也能在专题里播放
             "video": bool(lp and r["media_type"] != "note" and Path(lp).is_file())})
    for t in topics:
        t["items"] = items.get(t["id"], [])
    return {"nodes": nodes, "topics": topics, "max_depth": MAX_DEPTH}


def _commit(sql, args=()):
    with db._write_lock:
        c = db.conn()
        cur = c.execute(sql, args)
        c.commit()
        return cur


def node_create(parent_id, name):
    parent_id = int(parent_id) if parent_id else None
    if parent_id and _depth(parent_id) >= MAX_DEPTH:
        raise TopicError(f"分类最多 {MAX_DEPTH} 级")
    return _commit("INSERT INTO topic_nodes(parent_id,name,sort,created_at) VALUES(?,?,?,?)",
                   (parent_id, _name(name), int(time.time()), time.time())).lastrowid


def node_rename(node_id, name):
    _commit("UPDATE topic_nodes SET name=? WHERE id=?", (_name(name), int(node_id)))


def _subtree(node_id):
    out, todo, c = [], [int(node_id)], db.conn()
    while todo:
        n = todo.pop()
        out.append(n)
        todo += [r["id"] for r in c.execute("SELECT id FROM topic_nodes WHERE parent_id=?", (n,))]
    return out


def node_delete(node_id):
    """删分类：连同下面的子分类、专题一起删（视频本身不动）。"""
    ids = _subtree(node_id)
    marks = ",".join("?" * len(ids))
    tids = [r["id"] for r in db.conn().execute(f"SELECT id FROM topics WHERE node_id IN ({marks})", ids)]
    for t in tids:
        topic_delete(t)
    _commit(f"DELETE FROM topic_nodes WHERE id IN ({marks})", ids)


def topic_create(node_id, name):
    if not db.conn().execute("SELECT 1 FROM topic_nodes WHERE id=?", (int(node_id),)).fetchone():
        raise TopicError("先选一个分类")
    return _commit("INSERT INTO topics(node_id,name,sort,created_at) VALUES(?,?,?,?)",
                   (int(node_id), _name(name), int(time.time()), time.time())).lastrowid


def topic_rename(topic_id, name):
    _commit("UPDATE topics SET name=? WHERE id=?", (_name(name), int(topic_id)))


def topic_note(topic_id, text):
    """保存专题的配套笔记；返回保存时间。"""
    now = time.time()
    cur = _commit("UPDATE topics SET note=?, note_at=? WHERE id=?", (str(text or "")[:200000], now, int(topic_id)))
    if not cur.rowcount:
        raise TopicError("找不到这个专题")
    return now


def quote_add(topic_id, item_id, text):
    """记下「这段摘抄来自哪篇文章」（笔记正文里不写出处）。"""
    text = str(text or "").strip()[:2000]
    if text:
        _commit("INSERT INTO note_quotes(topic_id,item_id,text,created_at) VALUES(?,?,?,?)",
                (int(topic_id), int(item_id), text, time.time()))


def topic_delete(topic_id):
    _commit("DELETE FROM note_quotes WHERE topic_id=?", (int(topic_id),))
    _commit("DELETE FROM topic_items WHERE topic_id=?", (int(topic_id),))
    _commit("DELETE FROM topics WHERE id=?", (int(topic_id),))


def items_add(topic_id, ids):
    topic_id = int(topic_id)
    if not db.conn().execute("SELECT 1 FROM topics WHERE id=?", (topic_id,)).fetchone():
        raise TopicError("找不到这个专题")
    r = db.conn().execute("SELECT COALESCE(MAX(sort),0) m FROM topic_items WHERE topic_id=?", (topic_id,)).fetchone()
    n, added = r["m"], 0
    for i in ids:
        n += 1
        cur = _commit("INSERT OR IGNORE INTO topic_items(topic_id,item_id,sort,added_at) VALUES(?,?,?,?)",
                      (topic_id, int(i), n, time.time()))
        added += cur.rowcount
    return added


def items_remove(topic_id, ids):
    for i in ids:
        _commit("DELETE FROM topic_items WHERE topic_id=? AND item_id=?", (int(topic_id), int(i)))


def forget_item(item_id):
    """条目被彻底删除：从所有专题里拿掉，高亮也删掉。"""
    _commit("DELETE FROM topic_items WHERE item_id=?", (int(item_id),))
    _commit("DELETE FROM highlights WHERE item_id=?", (int(item_id),))
    _commit("DELETE FROM note_quotes WHERE item_id=?", (int(item_id),))


# ---------------------------------------------------------------- 高亮
def highlights():
    """{条目 id: [高亮的句子, ...]}，生成学习库页面时带上。"""
    out = {}
    for r in db.conn().execute("SELECT item_id, text FROM highlights ORDER BY id"):
        out.setdefault(r["item_id"], []).append(r["text"])
    return out


def hl_add(item_id, text):
    text = str(text or "").strip()[:2000]
    if not text:
        raise TopicError("没选中文字")
    if not db.conn().execute("SELECT 1 FROM highlights WHERE item_id=? AND text=?", (int(item_id), text)).fetchone():
        _commit("INSERT INTO highlights(item_id,text,created_at) VALUES(?,?,?)", (int(item_id), text, time.time()))


def hl_remove(item_id, text):
    _commit("DELETE FROM highlights WHERE item_id=? AND text=?", (int(item_id), str(text or "").strip()))


# ---------------------------------------------------------------- 复制到桌面
def desktop_dir():
    """真正的桌面位置（开了 OneDrive 备份时桌面在 OneDrive 里）。"""
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                p = Path(os.path.expandvars(winreg.QueryValueEx(k, "Desktop")[0]))
                if p.exists():
                    return p
        except OSError:
            pass
    for p in (Path.home() / "Desktop", Path.home() / "OneDrive" / "Desktop"):
        if p.exists():
            return p
    return Path.home()


def _node_path(node_id):
    names, c = [], db.conn()
    while node_id:
        r = c.execute("SELECT parent_id,name FROM topic_nodes WHERE id=?", (node_id,)).fetchone()
        if not r:
            break
        names.append(notes.safe_name(r["name"], 60))
        node_id = r["parent_id"]
    return list(reversed(names))


def _source(it):
    from .worker import downloaded_path
    p = it.get("local_path")
    if p and Path(p).exists():
        return Path(p)
    if it.get("aweme_id"):
        d = downloaded_path(it["aweme_id"])
        if d and Path(d).exists():
            return Path(d)
    return None


def copy_to_desktop(topic_id):
    """把专题里的视频按顺序复制到 桌面/专题学习/一级/二级/三级/专题名/，文件名前加序号。已经复制过的（大小一样）跳过。"""
    t = db.conn().execute("SELECT id,node_id,name,COALESCE(note,'') note FROM topics WHERE id=?", (int(topic_id),)).fetchone()
    if not t:
        raise TopicError("找不到这个专题")
    dest = desktop_dir() / ROOT_DIR_NAME
    for part in _node_path(t["node_id"]):
        dest = dest / part
    dest = dest / notes.safe_name(t["name"], 60)
    dest.mkdir(parents=True, exist_ok=True)
    if t["note"].strip():  # 配套笔记一起放进去，剪辑时对着看
        (dest / "笔记.md").write_text(f"# {t['name']}\n\n{t['note'].strip()}\n", encoding="utf-8")
    ids = [r["item_id"] for r in db.conn().execute(
        "SELECT item_id FROM topic_items WHERE topic_id=? ORDER BY sort, added_at", (t["id"],))]
    copied, same, missing = 0, 0, []
    width = max(2, len(str(len(ids))))
    for n, iid in enumerate(ids, 1):
        it = db.get(iid)
        src = _source(it) if it else None
        if not src:
            missing.append((it or {}).get("title") or f"#{iid}")
            continue
        name = f"{n:0{width}d} " + (src.stem if src.is_file() else src.name)[:80]
        target = dest / (notes.safe_name(name, 90) + (src.suffix if src.is_file() else ""))
        if src.is_dir():
            if target.exists():
                same += 1
                continue
            shutil.copytree(src, target)
        else:
            if target.exists() and target.stat().st_size == src.stat().st_size:
                same += 1
                continue
            shutil.copy2(src, target)
        copied += 1
    return {"dir": str(dest), "copied": copied, "same": same, "missing": missing, "total": len(ids)}
