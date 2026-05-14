"""
================================================================================
文件：tradenest/api/routes/tools.py
作用：工具管理 + 调试接口
================================================================================

接口列表：
  GET  /api/tools              列出所有已注册工具（含参数 schema）
  POST /api/tools/{name}/run   直接调用指定工具（调试用）
================================================================================
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from tradenest.tools import execute_tool, get_all_tools, get_tool_by_name

router = APIRouter(prefix="/api/tools", tags=["tools"])


class RunToolRequest(BaseModel):
    arguments: dict[str, Any] = {}


@router.get("")
def list_tools():
    """列出所有已注册工具及其参数 schema。"""
    tools = get_all_tools()
    return {
        "count": len(tools),
        "tools": [
            {
                "name": t.definition.name,
                "description": t.definition.description,
                "input_schema": t.definition.input_schema,
            }
            for t in tools
        ],
    }


@router.post("/{name}/run")
async def run_tool(name: str, req: RunToolRequest):
    """直接调用指定工具，返回结果（调试用）。"""
    tool = get_tool_by_name(name)
    if not tool:
        raise HTTPException(status_code=404, detail=f"工具不存在: {name}")

    start = time.time()
    result = await execute_tool(name, req.arguments)
    elapsed_ms = int((time.time() - start) * 1000)

    return {
        "tool": name,
        "arguments": req.arguments,
        "content": result.content,
        "is_error": result.is_error,
        "elapsed_ms": elapsed_ms,
    }
