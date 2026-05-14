"""
tradenest/db — 本地 SQLite 持久化层

数据文件位置：~/.tradenest/history.db
包含两张表：
  sessions  — 对话会话（id, name, created_at, updated_at）
  messages  — 消息记录（id, session_id, role, content, created_at）
"""

from .store import SessionStore, get_store

__all__ = ["SessionStore", "get_store"]
