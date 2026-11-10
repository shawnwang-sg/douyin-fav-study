"""SQLite 条目库。每条视频一行，记录处理状态、文字稿、AI 笔记和学习进度。"""
import json
import sqlite3
import threading
import time

from .config import DB_PATH

# 状态：
#   queued       等待处理
#   fetching     读取视频信息
#   downloading  下载中
#   transcribing 转文字中
#   extracting   AI 提炼中
#   done         完成，可以学习
#   need_key     已转成文字，但没配 AI Key，等配好后再提炼
#   irrelevant   和关注主题无关（自动收起，不算失败）
#   skipped      广告 / 图文 / 纯音乐 / 无对白，自动跳过
#   failed       失败，可以重试
ACTIVE = ("fetching", "downloading", "transcribing", "extracting")

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL DEFAULT 'link',      -- link / favorite / local
  aweme_id TEXT UNIQUE,
  url TEXT,
  local_path TEXT,
  title TEXT,
  author TEXT,
  duration REAL,
  cover TEXT,
  status TEXT NOT NULL DEFAULT 'queued',
  reason TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  transcript TEXT,
  note_json TEXT,
  category TEXT,
  md_path TEXT,
  learned INTEGER NOT NULL DEFAULT 0,
  starred INTEGER NOT NULL DEFAULT 0,
  my_note TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  learned_at REAL
);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
-- 共识观点：每个分类一份，AI 从这个分类的多条笔记里找出互相印证 / 有分歧的观点
CREATE TABLE IF NOT EXISTS insights (
  category TEXT PRIMARY KEY,
  data_json TEXT NOT NULL,
  item_ids TEXT NOT NULL,     -- 整理时用到的笔记 id（逗号分隔），用来判断有没有新笔记
  model TEXT,
  md_path TEXT,
  created_at REAL NOT NULL
);
"""

_local = threading.local()
_write_lock = threading.Lock()


def conn():
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        _local.conn = c
    return c


EXTRA_COLUMNS = {
    "segments_json": "TEXT",   # 带时间戳的文字稿分段
    "deep_json": "TEXT",       # 精读结果
    "chat_json": "TEXT",       # 精读里的答题点评、问老师记录
    "media_type": "TEXT",      # video / note（图文）
    "images_json": "TEXT",     # 图文的图片（相对于笔记目录的路径列表）
    "fav_folder": "TEXT",      # 从哪个收藏夹分类 / 专辑同步来的（本地视频库的子文件夹）
    "platform": "TEXT",        # douyin / xhs（学习库不区分平台，只用来选下载器和显示来源）
    "fav_at": "REAL",          # 第一次在收藏列表里读到它的时间（每周增量整理用）
    "uid": "TEXT",             # 去重用的唯一标识：dy:作品ID / xhs:笔记ID / file:本地文件指纹（同一个视频永远同一个 uid）
}

UID_PREFIX = {"douyin": "dy", "xhs": "xhs"}


def file_fingerprint(path):
    """本地文件 / 图文文件夹的指纹：文件 = 大小 + 开头 1MB + 结尾 1MB 的 sha1；文件夹 = 里面的文件名和大小。挪位置、改名都不变。"""
    import hashlib
    from pathlib import Path
    p, h = Path(path), hashlib.sha1()
    if p.is_dir():
        for f in sorted(x for x in p.iterdir() if x.is_file()):
            h.update(f"{f.name}:{f.stat().st_size};".encode("utf-8"))
    else:
        size = p.stat().st_size
        h.update(str(size).encode())
        with open(p, "rb") as fh:
            h.update(fh.read(1 << 20))
            if size > 2 << 20:
                fh.seek(-(1 << 20), 2)
                h.update(fh.read(1 << 20))
    return h.hexdigest()[:20]


def make_uid(platform=None, aweme_id=None, local_path=None):
    """有作品 ID 用「平台:ID」；只有本地文件就用文件指纹；都没有返回 None。"""
    if aweme_id:
        from .config import platform_of
        p = platform or platform_of(str(aweme_id), local_path or "")
        return f"{UID_PREFIX.get(p, p)}:{aweme_id}"
    if local_path:
        try:
            return "file:" + file_fingerprint(local_path)
        except OSError:
            return None
    return None


def init():
    with _write_lock:
        conn().executescript(SCHEMA)
        have = {r["name"] for r in conn().execute("PRAGMA table_info(items)")}
        for col, typ in EXTRA_COLUMNS.items():
            if col not in have:  # 老版本的数据库自动升级
                conn().execute(f"ALTER TABLE items ADD COLUMN {col} {typ}")
        # 老数据补上平台
        from .config import platform_of
        for r in conn().execute("SELECT id, aweme_id, local_path FROM items WHERE platform IS NULL").fetchall():
            conn().execute("UPDATE items SET platform=? WHERE id=?",
                           (platform_of(r["aweme_id"] or "", r["local_path"] or ""), r["id"]))
        # 补 uid（第一次升级时给所有老条目算一遍），再加唯一索引：同一个视频进不来第二次
        for r in conn().execute("SELECT id, platform, aweme_id, local_path FROM items WHERE uid IS NULL").fetchall():
            u = make_uid(r["platform"], r["aweme_id"], r["local_path"])
            if u and not conn().execute("SELECT 1 FROM items WHERE uid=?", (u,)).fetchone():
                conn().execute("UPDATE items SET uid=? WHERE id=?", (u, r["id"]))
        conn().execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_items_uid ON items(uid) WHERE uid IS NOT NULL")
        # 程序上次被强制关掉时，卡在中间状态的条目放回队列
        conn().execute(
            f"UPDATE items SET status='queued' WHERE status IN ({','.join('?' * len(ACTIVE))})", ACTIVE)
        conn().commit()


JSON_FIELDS = {"note": "note_json", "segments": "segments_json", "deep": "deep_json", "chat": "chat_json",
               "images": "images_json"}


def _row(r):
    if r is None:
        return None
    d = dict(r)
    for name, col in JSON_FIELDS.items():
        if col in d:
            d[name] = json.loads(d.pop(col) or "null")
    return d


def add(source, *, aweme_id=None, url=None, local_path=None, title=None, author=None,
        duration=None, cover=None, platform=None):
    """新增一条；同一个视频（uid 相同：同平台同作品 ID，或同一个本地文件）不会重复添加。返回 (id, 是否新增)。"""
    from .config import platform_of
    now = time.time()
    platform = platform or platform_of(aweme_id or "", local_path or "")
    fav_at = now if source == "favorite" else None
    uid = make_uid(platform, aweme_id, local_path)
    with _write_lock:
        c = conn()
        r = c.execute("SELECT id FROM items WHERE uid=?", (uid,)).fetchone() if uid else None
        if r:
            return r["id"], False
        if aweme_id and local_path:  # 只在按 ID 没找到时才算文件指纹（读文件比较慢）：认出以前按文件导入的同一个视频
            fu = make_uid(platform, None, local_path)
            r = c.execute("SELECT id FROM items WHERE uid=?", (fu,)).fetchone() if fu else None
            if r:
                return r["id"], False
        if aweme_id:  # 兜底：还没补上 uid 的老条目
            r = c.execute("SELECT id FROM items WHERE aweme_id=?", (aweme_id,)).fetchone()
            if r:
                return r["id"], False
        if local_path:
            r = c.execute("SELECT id FROM items WHERE local_path=?", (local_path,)).fetchone()
            if r:
                return r["id"], False
        cur = c.execute(
            "INSERT INTO items(source,aweme_id,url,local_path,title,author,duration,cover,status,created_at,updated_at,"
            "platform,fav_at,uid) VALUES(?,?,?,?,?,?,?,?,'queued',?,?,?,?,?)",
            (source, aweme_id, url, local_path, title, author, duration, cover, now, now, platform, fav_at, uid))
        c.commit()
        return cur.lastrowid, True


def update(item_id, **fields):
    if not fields:
        return
    for name, col in JSON_FIELDS.items():
        if name in fields:
            v = fields.pop(name)
            fields[col] = json.dumps(v, ensure_ascii=False) if v is not None else None
    fields["updated_at"] = time.time()
    if fields.get("aweme_id") and "uid" not in fields:  # 后来才知道作品 ID（解析链接 / 认出老条目）：uid 换成「平台:ID」
        r = conn().execute("SELECT platform FROM items WHERE id=?", (item_id,)).fetchone()
        u = make_uid(r["platform"] if r else None, fields["aweme_id"])
        if u and not conn().execute("SELECT 1 FROM items WHERE uid=? AND id<>?", (u, item_id)).fetchone():
            fields["uid"] = u
    keys = list(fields)
    with _write_lock:
        conn().execute(f"UPDATE items SET {','.join(k + '=?' for k in keys)} WHERE id=?",
                       [fields[k] for k in keys] + [item_id])
        conn().commit()


def get(item_id):
    return _row(conn().execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone())


def delete(item_id):
    with _write_lock:
        conn().execute("DELETE FROM items WHERE id=?", (item_id,))
        conn().commit()


def next_queued(ids=None, exclude=(), local_only=False, prefer=None):
    """prefer：优先取 local_path 在这个文件夹下的（按库分路并行），没有了再取别的。"""
    if prefer:
        pre = prefer.replace("/", "\\").rstrip("\\") + "\\"
        for r in conn().execute("SELECT * FROM items WHERE status='queued' AND source='local' AND local_path LIKE ? "
                                "ORDER BY id", (pre.replace("%", "") + "%",)):
            if r["id"] not in exclude and (ids is None or r["id"] in ids):
                return _row(r)
    return _next_queued(ids, exclude, local_only)


def _next_queued(ids=None, exclude=(), local_only=False):
    """下一条待处理的。ids：只在这几条里找（按 ids 的顺序）。
    exclude：跳过这些 id（并行时别的线程已经领走、状态还没改的）；local_only：只要本地文件。"""
    loc = " AND source='local'" if local_only else ""
    if ids is None:
        for r in conn().execute(f"SELECT * FROM items WHERE status='queued'{loc} ORDER BY id"):
            if r["id"] not in exclude:
                return _row(r)
        return None
    for i in ids:
        if i in exclude:
            continue
        r = conn().execute(f"SELECT * FROM items WHERE id=? AND status='queued'{loc}", (i,)).fetchone()
        if r:
            return _row(r)
    return None


def count_queued(ids):
    return sum(1 for i in ids if conn().execute(
        "SELECT 1 FROM items WHERE id=? AND status='queued'", (i,)).fetchone())


def list_items(status=None, category=None, q=None, learned=None, starred=None, limit=1000):
    sql = ("SELECT id,source,aweme_id,url,title,author,duration,cover,status,reason,category,learned,starred,"
           "created_at,updated_at,note_json,media_type,platform,(deep_json IS NOT NULL) AS has_deep FROM items WHERE 1=1")
    args = []
    if status:
        statuses = status.split(",")
        sql += f" AND status IN ({','.join('?' * len(statuses))})"
        args += statuses
    if category:
        sql += " AND category=?"
        args.append(category)
    if learned is not None:
        sql += " AND learned=?"
        args.append(1 if learned else 0)
    if starred:
        sql += " AND starred=1"
    if q:
        like = f"%{q}%"
        sql += " AND (title LIKE ? OR author LIKE ? OR note_json LIKE ? OR transcript LIKE ? OR my_note LIKE ?)"
        args += [like] * 5
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    out = []
    for r in conn().execute(sql, args).fetchall():
        d = _row(r)
        note = d.pop("note") or {}
        d["summary"] = note.get("summary", "")
        d["note_title"] = note.get("title", "")
        d["tags"] = note.get("tags", [])
        d["difficulty"] = note.get("difficulty", "")
        out.append(d)
    return out


def done_items():
    """学习库里的全部笔记（含 JSON 字段），图谱、共识观点和周报用。"""
    return [_row(r) for r in conn().execute(
        "SELECT id,title,author,category,learned,learned_at,starred,created_at,fav_at,platform,md_path,media_type,"
        "note_json,deep_json,chat_json FROM items WHERE status='done' ORDER BY id")]


def get_insight(category):
    r = conn().execute("SELECT * FROM insights WHERE category=?", (category,)).fetchone()
    if r is None:
        return None
    d = dict(r)
    d["data"] = json.loads(d.pop("data_json"))
    d["item_ids"] = [int(x) for x in d["item_ids"].split(",") if x]
    return d


def list_insights():
    return [get_insight(r["category"]) for r in conn().execute("SELECT category FROM insights ORDER BY created_at DESC")]


def save_insight(category, data, item_ids, model, md_path):
    with _write_lock:
        conn().execute(
            "INSERT OR REPLACE INTO insights(category,data_json,item_ids,model,md_path,created_at) VALUES(?,?,?,?,?,?)",
            (category, json.dumps(data, ensure_ascii=False), ",".join(map(str, item_ids)), model, md_path, time.time()))
        conn().commit()


def stats():
    c = conn()
    by_status = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM items GROUP BY status")}
    cats = [dict(r) for r in c.execute(
        "SELECT category, COUNT(*) n, SUM(learned) learned FROM items WHERE status='done' "
        "GROUP BY category ORDER BY n DESC")]
    learned = c.execute("SELECT COUNT(*) FROM items WHERE status='done' AND learned=1").fetchone()[0]
    starred = c.execute("SELECT COUNT(*) FROM items WHERE status='done' AND starred=1").fetchone()[0]
    return {"by_status": by_status, "categories": cats, "learned": learned, "starred": starred,
            "total": sum(by_status.values())}
