"""SQLite 存储：发送历史与幂等去重。

幂等口径是 (rule_id, channel_id, path, sha256) 四元组：
同一路径内容没变就只发一次，内容变了（新版本覆盖）则重新发送。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional

DATA_DIR = os.environ.get("DIR2CHAT_CONFIG_DIR", "/config")
DB_PATH = os.path.join(DATA_DIR, "dir2chat.db")

_LOCAL = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS sent (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id     TEXT NOT NULL,
    channel_id  TEXT NOT NULL,
    path        TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    size        INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL,
    error       TEXT,
    sent_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sent_lookup
    ON sent(rule_id, channel_id, path, sha256);
CREATE INDEX IF NOT EXISTS idx_sent_time ON sent(sent_at DESC);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    level   TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(created_at DESC);
"""


def _conn() -> sqlite3.Connection:
    conn = getattr(_LOCAL, "conn", None)
    if conn is None:
        os.makedirs(DATA_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        _LOCAL.conn = conn
    return conn


def already_sent(rule_id: str, channel_id: str, path: str, sha256: str) -> bool:
    row = _conn().execute(
        "SELECT 1 FROM sent WHERE rule_id=? AND channel_id=? AND path=? AND sha256=? "
        "AND status='ok' LIMIT 1",
        (rule_id, channel_id, path, sha256),
    ).fetchone()
    return row is not None


def record(rule_id: str, channel_id: str, path: str, sha256: str, size: int,
           kind: str, status: str, error: Optional[str] = None) -> None:
    _conn().execute(
        "INSERT INTO sent(rule_id, channel_id, path, sha256, size, kind, status, error) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (rule_id, channel_id, path, sha256, size, kind, status, (error or "")[:800]),
    )
    _conn().commit()


def recent(limit: int = 100, only_fail: bool = False) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM sent"
    if only_fail:
        sql += " WHERE status != 'ok'"
    sql += " ORDER BY id DESC LIMIT ?"
    rows = _conn().execute(sql, (limit,)).fetchall()
    return [dict(r) for r in rows]


def stats() -> Dict[str, int]:
    conn = _conn()
    ok = conn.execute("SELECT COUNT(*) c FROM sent WHERE status='ok'").fetchone()["c"]
    fail = conn.execute("SELECT COUNT(*) c FROM sent WHERE status!='ok'").fetchone()["c"]
    today = conn.execute(
        "SELECT COUNT(*) c FROM sent WHERE status='ok' AND date(sent_at)=date('now')"
    ).fetchone()["c"]
    return {"ok": ok, "fail": fail, "today": today}


def log(level: str, message: str) -> None:
    try:
        _conn().execute("INSERT INTO events(level, message) VALUES(?,?)",
                        (level, message[:1000]))
        _conn().commit()
    except Exception:
        pass


def recent_events(limit: int = 200) -> List[Dict[str, Any]]:
    rows = _conn().execute(
        "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def purge_rule(rule_id: str) -> int:
    """清空某条规则的发送记录，让下一轮扫描重新发送。"""
    cur = _conn().execute("DELETE FROM sent WHERE rule_id=?", (rule_id,))
    _conn().commit()
    return cur.rowcount or 0


def clear_sent() -> int:
    """清空全部发送记录，让下一轮扫描重新发送目录里所有文件。"""
    cur = _conn().execute("DELETE FROM sent")
    _conn().commit()
    return cur.rowcount or 0


def clear_events() -> int:
    """清空全部事件日志。"""
    cur = _conn().execute("DELETE FROM events")
    _conn().commit()
    return cur.rowcount or 0


def purge(keep: int = 5000) -> int:
    """保留最近 keep 条记录，避免长期运行后库无限增长。"""
    cur = _conn().execute(
        "DELETE FROM sent WHERE id NOT IN (SELECT id FROM sent ORDER BY id DESC LIMIT ?)",
        (keep,),
    )
    _conn().commit()
    cur2 = _conn().execute(
        "DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT ?)",
        (keep,),
    )
    _conn().commit()
    return (cur.rowcount or 0) + (cur2.rowcount or 0)
