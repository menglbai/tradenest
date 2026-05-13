"""
================================================================================
文件：tradenest/api/routes/chat.py
作用：/chat 对话接口（支持流式 SSE 和非流式 JSON）
================================================================================

【核心业务功能】
对外暴露两个端点：
1. POST /chat            - 非流式，一次返回完整结果
2. POST /chat/stream     - 流式 SSE，逐 token 返回

【SSE 协议】
每条事件格式：
    event: <type>
    data: <json>

事件类型：
- text_delta       : LLM 流式文本
- tool_call_start  : 模型决定调用工具
- tool_result      : 工具执行结果
- round_end        : 一轮 LLM 调用结束
- complete         : 整个会话结束（带最终文本 + 元数据）
- error            : 报错

================================================================================
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from tradenest.agents.runtime import run_agent, run_agent_stream
from tradenest.compliance import safe_output
from tradenest.core.config import settings
from tradenest.core.logging import get_logger
from tradenest.llm.router import TaskType

router = APIRouter(prefix="/chat", tags=["chat"])
log = get_logger("tradenest.api.chat")


# ============================================================
# 请求模型
# ============================================================

class ChatRequest(BaseModel):
    """对话请求体"""
    
    message: str = Field(..., min_length=1, max_length=10000, description="用户消息")
    
    task: str = Field(
        default=TaskType.ANALYST.value,
        description="任务类型：simple_query / analyst / synthesis / socratic / summary",
    )
    
    provider_id: str | None = Field(
        default=None,
        description="显式指定 LLM Provider ID（如 'internal-xhs'），None 走路由表",
    )
    
    model: str | None = Field(
        default=None,
        description="显式指定模型名，None 用 Provider 默认",
    )
    
    max_rounds: int | None = Field(
        default=None,
        ge=1,
        le=20,
        description="最大 Agent loop 轮数，None 用 settings 默认",
    )


class ChatResponse(BaseModel):
    """非流式响应"""
    
    final_text: str
    rounds: int
    tool_calls: list[dict[str, Any]]
    total_input_tokens: int
    total_output_tokens: int
    duration_ms: float
    stop_reason: str
    compliance_passed: bool
    compliance_violations: list[dict[str, str]] = []


# ============================================================
# 路由：非流式
# ============================================================

@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """非流式对话接口。
    
    用于：CLI / 一次性查询 / 不需要打字效果的场景。
    """
    try:
        task = TaskType(request.task)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"未知任务类型：{request.task}")
    
    log.info("chat_request", task=task.value, message_preview=request.message[:80])
    
    # 显式指定 Provider 时
    provider = None
    if request.provider_id:
        from tradenest.llm.registry import get_registry
        try:
            provider = get_registry().get(request.provider_id)
        except KeyError:
            raise HTTPException(
                status_code=400,
                detail=f"未知 Provider: {request.provider_id}",
            )
    
    result = await run_agent(
        request.message,
        task=task,
        provider=provider,
        model=request.model,
        max_rounds=request.max_rounds,
    )
    
    if result.error:
        raise HTTPException(status_code=500, detail=result.error)
    
    # 合规审查
    safe_text, check = safe_output(result.final_text, strict=settings.compliance_strict)
    
    return ChatResponse(
        final_text=safe_text,
        rounds=result.rounds,
        tool_calls=result.tool_calls,
        total_input_tokens=result.total_input_tokens,
        total_output_tokens=result.total_output_tokens,
        duration_ms=result.duration_ms,
        stop_reason=result.stop_reason,
        compliance_passed=check.passed,
        compliance_violations=check.violations,
    )


# ============================================================
# 路由：流式 SSE
# ============================================================

@router.post("/stream")
async def chat_stream(request: ChatRequest) -> EventSourceResponse:
    """流式 SSE 对话接口。
    
    用于：实时打字效果 / 桌面客户端 / 浏览器扩展。
    """
    try:
        task = TaskType(request.task)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"未知任务类型：{request.task}")
    
    provider = None
    if request.provider_id:
        from tradenest.llm.registry import get_registry
        try:
            provider = get_registry().get(request.provider_id)
        except KeyError:
            raise HTTPException(
                status_code=400,
                detail=f"未知 Provider: {request.provider_id}",
            )
    
    log.info("chat_stream_request", task=task.value, message_preview=request.message[:80])
    
    async def event_generator() -> Any:
        """把 run_agent_stream 的事件转成 SSE 格式。"""
        full_text_parts: list[str] = []
        
        try:
            async for event in run_agent_stream(
                request.message,
                task=task,
                provider=provider,
                model=request.model,
                max_rounds=request.max_rounds,
            ):
                etype = event.get("type", "unknown")
                
                # 累积 final_text 用于合规审查
                if etype == "text_delta":
                    full_text_parts.append(event.get("text", ""))
                
                # complete 事件前做合规审查
                if etype == "complete":
                    final_text = event.get("final_text", "".join(full_text_parts))
                    safe_text, check = safe_output(final_text, strict=settings.compliance_strict)
                    event["final_text"] = safe_text
                    event["compliance_passed"] = check.passed
                    event["compliance_violations"] = check.violations
                
                yield {
                    "event": etype,
                    "data": json.dumps(event, ensure_ascii=False, default=str),
                }
        except Exception as e:
            log.error("chat_stream_error", error=str(e), exc_info=e)
            yield {
                "event": "error",
                "data": json.dumps({"message": str(e)}, ensure_ascii=False),
            }
    
    return EventSourceResponse(event_generator())
