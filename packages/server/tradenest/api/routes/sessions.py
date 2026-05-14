"""
================================================================================
文件：tradenest/api/routes/sessions.py
作用：会话管理 REST 接口
================================================================================

接口列表：
  GET    /api/sessions              列出所有会话
  POST   /api/sessions              创建新会话
  GET    /api/sessions/{id}         获取会话详情 + 消息列表
  PATCH  /api/sessions/{id}         重命名会话
  DELETE /api/sessions/{id}         删除会话
  GET    /api/sessions/stats        数据库统计
================================================================================
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from tradenest.db import get_store

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class CreateSessionReq(BaseModel):
    name: str = "新对话"


class RenameSessionReq(BaseModel):
    name: str


@router.get("")
def list_sessions(limit: int = 50):
    store = get_store()
    sessions = store.list_sessions(limit=limit)
    # 附带每个会话的消息数
    for s in sessions:
        s["message_count"] = store.count_messages(s["id"])
    return {"sessions": sessions}


@router.post("")
def create_session(req: CreateSessionReq):
    store = get_store()
    sid = store.create_session(req.name)
    return {"session_id": sid, "name": req.name}


@router.get("/stats")
def db_stats():
    return get_store().stats()


@router.get("/{session_id}")
def get_session(session_id: str, msg_limit: int = 100):
    store = get_store()
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = store.get_messages(session_id, limit=msg_limit)
    return {"session": session, "messages": messages}


@router.patch("/{session_id}")
def rename_session(session_id: str, req: RenameSessionReq):
    store = get_store()
    if not store.get_session(session_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    store.rename_session(session_id, req.name)
    return {"ok": True}


@router.delete("/{session_id}")
def delete_session(session_id: str):
    store = get_store()
    if not store.get_session(session_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    store.delete_session(session_id)
    return {"ok": True}
