"""
================================================================================
文件：tradenest/db/store.py
作用：SQLite 对话历史持久化
================================================================================

数据库文件位置：~/.tradenest/history.db（自动创建，无需配置）

表结构：
  sessions  — 会话列表
  messages  — 消息记录（每条对话的 user/assistant 消息）

用法：
  store = get_store()
  sid = store.create_session("茅台分析")
  store.add_message(sid, "user", "茅台今天怎么样？")
  store.add_message(sid, "assistant", "今日茅台...")
  msgs = store.get_messages(sid)
  sessions = store.list_sessions()
================================================================================
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any


# 数据库文件路径：~/.tradenest/history.db
DB_DIR  = Path.home() / ".tradenest"
DB_PATH = DB_DIR / "history.db"

DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '新对话',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
    content     TEXT NOT NULL,
    created_at  REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
"""


class SessionStore:
    """对话历史存储，线程安全（每次操作独立连接）。"""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")   # 写时不阻塞读
        return conn

    def _init_db(self) -> None:
        """建库建表（幂等，可重复调用）。"""
        DB_DIR.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(DDL)

    # ────────────────────────────────────────
    #  Session CRUD
    # ────────────────────────────────────────

    def create_session(self, name: str = "新对话") -> str:
        """创建新会话，返回 session_id。"""
        sid = str(uuid.uuid4())
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, name, created_at, updated_at) VALUES (?,?,?,?)",
                (sid, name, now, now),
            )
        return sid

    def rename_session(self, session_id: str, name: str) -> None:
        """重命名会话。"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET name=?, updated_at=? WHERE id=?",
                (name[:60], time.time(), session_id),
            )

    def delete_session(self, session_id: str) -> None:
        """删除会话及其所有消息（CASCADE）。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        """按最后更新时间倒序返回会话列表。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, created_at, updated_at FROM sessions ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, name, created_at, updated_at FROM sessions WHERE id=?",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    # ────────────────────────────────────────
    #  Message CRUD
    # ────────────────────────────────────────

    def add_message(self, session_id: str, role: str, content: str) -> str:
        """追加一条消息，同时更新会话 updated_at，返回 message_id。"""
        mid = str(uuid.uuid4())
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, created_at) VALUES (?,?,?,?,?)",
                (mid, session_id, role, content, now),
            )
            conn.execute(
                "UPDATE sessions SET updated_at=? WHERE id=?",
                (now, session_id),
            )
        return mid

    def get_messages(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """返回会话的消息列表（按时间正序），最多 limit 条（取最新的）。"""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, role, content, created_at FROM messages
                WHERE session_id=?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_messages_for_llm(self, session_id: str, limit: int = 40) -> list[dict[str, str]]:
        """返回适合传给 LLM 的 messages 格式：[{role, content}, ...]。"""
        msgs = self.get_messages(session_id, limit=limit)
        return [{"role": m["role"], "content": m["content"]} for m in msgs]

    def count_messages(self, session_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id=?", (session_id,)
            ).fetchone()
        return row[0] if row else 0

    # ────────────────────────────────────────
    #  Stats
    # ────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            n_sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            n_messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            db_size = self.db_path.stat().st_size if self.db_path.exists() else 0
        return {
            "db_path": str(self.db_path),
            "sessions": n_sessions,
            "messages": n_messages,
            "db_size_kb": round(db_size / 1024, 1),
        }


@lru_cache(maxsize=1)
def get_store() -> SessionStore:
    """全局单例 Store（进程内复用）。"""
    return SessionStore()
