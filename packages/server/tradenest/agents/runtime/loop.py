"""
================================================================================
文件：tradenest/agents/runtime/loop.py
作用：Agent 主循环（LLM ↔ Tool 反复对话）
================================================================================

【核心业务功能】
TradeNest 的 Agent 内核：让 LLM 自主决定何时调用工具，反复循环直到给出最终回答。

简化版伪代码：
```
messages = [user message]
while True:
    response = llm.chat(messages, tools=...)
    if response.stop_reason == "end_turn":
        return response.text
    if response.stop_reason == "tool_use":
        for tool_call in response.tool_uses:
            result = execute_tool(tool_call.name, tool_call.input)
            messages.append(tool_result)
        # 继续 loop
```

【提供 2 个对外接口】
1. `run_agent`: 非流式（一次性返回 AgentRunResult）
2. `run_agent_stream`: 流式（用 async generator 吐 SSE 事件）

【流式协议】
yield 这些事件给上层（API / CLI）：
- {"type": "text_delta", "text": "..."}    : LLM 流式文本
- {"type": "tool_call", "name": "...", ...}: LLM 决定调工具
- {"type": "tool_result", ...}             : 工具结果
- {"type": "complete", "final_text": "...", ...}: 全部完成

================================================================================
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from tradenest.agents.prompts import TRADENEST_BASE_SYSTEM, build_system_prompt
from tradenest.core.config import settings
from tradenest.core.logging import get_logger
from tradenest.llm.base import LLMProvider, Message, TextBlock, ToolUseBlock
from tradenest.llm.router import TaskType, get_provider_for
from tradenest.tools import execute_tool, get_tool_definitions

log = get_logger("tradenest.agents.runtime")


# ============================================================
# 数据模型
# ============================================================

@dataclass
class AgentRunResult:
    """Agent 一次完整运行的结果。"""
    
    final_text: str
    """最终给用户看的文本"""
    
    rounds: int
    """LLM 调用了几轮（每次工具调用 + 1）"""
    
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    """所有工具调用记录"""
    
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    
    duration_ms: float = 0.0
    """总耗时（毫秒）"""
    
    stop_reason: str = ""
    """最后一次 LLM 调用的 stop_reason"""
    
    error: str | None = None


# ============================================================
# 主接口：非流式
# ============================================================

async def run_agent(
    user_message: str,
    *,
    task: TaskType = TaskType.ANALYST,
    system_prompt: str | None = None,
    history: list[Message] | None = None,
    max_rounds: int | None = None,
    provider: LLMProvider | None = None,
    model: str | None = None,
) -> AgentRunResult:
    """非流式 Agent 调用。
    
    Args:
        user_message: 用户输入文本
        task: 任务类型（决定路由到哪个模型）
        system_prompt: 自定义 system prompt（None 用默认）
        history: 之前的对话历史（None 表示新对话）
        max_rounds: 最大循环轮数（None 用 settings.agent_max_loop_turns）
        provider: 显式指定 Provider（None 用 task 路由）
        model: 显式指定模型
    
    Returns:
        AgentRunResult
    """
    start = time.time()
    
    # 1. 决定 Provider 和 模型
    if provider is None:
        provider, default_model = get_provider_for(task)
        if model is None:
            model = default_model
    elif model is None:
        model = provider.default_model
    
    # 2. 组装 messages
    messages: list[Message] = list(history) if history else []
    messages.append(Message(role="user", content=user_message))
    
    # 3. 系统 prompt
    if system_prompt is None:
        system_prompt = build_system_prompt()
    
    # 4. 工具列表（全部）
    tools = get_tool_definitions()
    
    # 5. Loop
    max_rounds = max_rounds or settings.agent_max_loop_turns
    tool_call_log: list[dict[str, Any]] = []
    total_in = 0
    total_out = 0
    final_text = ""
    last_stop = ""
    
    log.info(
        "agent_run_start",
        task=task.value,
        provider=provider.provider_id,
        model=model,
        message_preview=user_message[:80],
    )
    
    for round_i in range(1, max_rounds + 1):
        try:
            response = await provider.chat(
                messages=messages,
                system=system_prompt,
                tools=tools,
                model=model,
                max_tokens=settings.agent_max_tokens,
            )
        except Exception as e:
            log.error("agent_llm_call_failed", round=round_i, error=str(e))
            return AgentRunResult(
                final_text=f"LLM 调用失败：{e}",
                rounds=round_i - 1,
                tool_calls=tool_call_log,
                duration_ms=(time.time() - start) * 1000,
                error=str(e),
            )
        
        total_in += response.usage.get("input_tokens", 0)
        total_out += response.usage.get("output_tokens", 0)
        last_stop = response.stop_reason
        
        # 收集 LLM 这轮输出的文本 + 工具调用
        text_parts: list[str] = []
        tool_uses: list[ToolUseBlock] = []
        for block in response.content:
            if isinstance(block, TextBlock):
                text_parts.append(block.text)
            elif isinstance(block, ToolUseBlock):
                tool_uses.append(block)
        
        round_text = "".join(text_parts)
        
        # 如果模型没要求调工具 → 结束
        if response.stop_reason != "tool_use" or not tool_uses:
            final_text = round_text
            log.info(
                "agent_run_complete",
                rounds=round_i,
                stop_reason=response.stop_reason,
                final_text_len=len(final_text),
            )
            break
        
        # 模型要求调工具：把这轮 assistant 消息加到历史
        messages.append(Message(
            role="assistant",
            content=round_text,
            tool_uses=tool_uses,
        ))
        
        # 执行所有工具
        for tu in tool_uses:
            tc_record: dict[str, Any] = {
                "round": round_i,
                "name": tu.name,
                "input": tu.input,
            }
            tool_call_log.append(tc_record)
            
            tool_result = await execute_tool(tu.name, tu.input)
            tc_record["result_preview"] = tool_result.content[:200]
            tc_record["is_error"] = tool_result.is_error
            
            # 把工具结果作为下一轮输入
            messages.append(Message(
                role="tool",
                content=tool_result.content,
                tool_use_id=tu.id,
            ))
    else:
        # 用完所有 round 还没结束
        log.warning("agent_max_rounds_reached", max_rounds=max_rounds)
        final_text = (
            f"（因达到最大循环数 {max_rounds} 而强制结束。"
            f"如果你希望我继续深入分析，请重新提问。）"
        )
    
    duration_ms = (time.time() - start) * 1000
    
    return AgentRunResult(
        final_text=final_text,
        rounds=round_i,
        tool_calls=tool_call_log,
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        duration_ms=duration_ms,
        stop_reason=last_stop,
    )


# ============================================================
# 流式接口
# ============================================================

async def run_agent_stream(
    user_message: str,
    *,
    task: TaskType = TaskType.ANALYST,
    system_prompt: str | None = None,
    history: list[Message] | None = None,
    max_rounds: int | None = None,
    provider: LLMProvider | None = None,
    model: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """流式 Agent 调用。
    
    Yields:
        SSE 风格事件 dict：
        - {"type": "text_delta", "text": "..."}
        - {"type": "tool_call_start", "name": ..., "input": ...}
        - {"type": "tool_result", "name": ..., "content_preview": ..., "is_error": bool}
        - {"type": "round_end", "round": int, "stop_reason": str}
        - {"type": "complete", "final_text": ..., "tool_calls": [...], "rounds": int, ...}
        - {"type": "error", "message": ...}
    """
    start = time.time()
    
    if provider is None:
        provider, default_model = get_provider_for(task)
        if model is None:
            model = default_model
    elif model is None:
        model = provider.default_model
    
    messages: list[Message] = list(history) if history else []
    messages.append(Message(role="user", content=user_message))
    
    if system_prompt is None:
        system_prompt = build_system_prompt()
    
    tools = get_tool_definitions()
    max_rounds = max_rounds or settings.agent_max_loop_turns
    
    full_text_acc: list[str] = []
    tool_call_log: list[dict[str, Any]] = []
    last_stop = ""
    total_in = 0
    total_out = 0

    log.info("agent_stream_start", task=task.value, message_preview=user_message[:80])
    
    for round_i in range(1, max_rounds + 1):
        # 累积本轮文本和工具调用
        round_text_parts: list[str] = []
        round_tool_uses: list[ToolUseBlock] = []
        
        try:
            async for event in provider.chat_stream(
                messages=messages,
                system=system_prompt,
                tools=tools,
                model=model,
                max_tokens=settings.agent_max_tokens,
            ):
                etype = event.get("type")
                
                if etype == "text_delta":
                    delta = event.get("text", "")
                    round_text_parts.append(delta)
                    yield {"type": "text_delta", "text": delta}
                
                elif etype == "tool_use_complete":
                    tu = event.get("tool_use")
                    if tu:
                        round_tool_uses.append(tu)
                        yield {
                            "type": "tool_call_start",
                            "name": tu.name,
                            "input": tu.input,
                        }
                
                elif etype == "message_stop":
                    last_stop = event.get("stop_reason", "")
                    # 累积 token 用量
                    usage = event.get("usage", {})
                    total_in += usage.get("input_tokens", 0)
                    total_out += usage.get("output_tokens", 0)
        
        except Exception as e:
            log.error("agent_stream_llm_failed", round=round_i, error=str(e))
            yield {"type": "error", "message": f"LLM 调用失败：{e}"}
            return
        
        round_text = "".join(round_text_parts)
        full_text_acc.append(round_text)
        
        yield {"type": "round_end", "round": round_i, "stop_reason": last_stop}
        
        # 如果不需要调工具 → 结束
        if last_stop != "tool_use" or not round_tool_uses:
            break
        
        # 把 assistant 消息加进 history
        messages.append(Message(
            role="assistant",
            content=round_text,
            tool_uses=round_tool_uses,
        ))
        
        # 执行所有工具
        for tu in round_tool_uses:
            tool_result = await execute_tool(tu.name, tu.input)
            
            tc_record = {
                "round": round_i,
                "name": tu.name,
                "input": tu.input,
                "result_preview": tool_result.content[:200],
                "is_error": tool_result.is_error,
            }
            tool_call_log.append(tc_record)
            
            yield {
                "type": "tool_result",
                "name": tu.name,
                "content_preview": tool_result.content[:500],
                "is_error": tool_result.is_error,
            }
            
            messages.append(Message(
                role="tool",
                content=tool_result.content,
                tool_use_id=tu.id,
            ))
    
    duration_ms = (time.time() - start) * 1000
    final_text = "".join(full_text_acc)
    
    log.info(
        "agent_stream_complete",
        rounds=round_i,
        duration_ms=duration_ms,
        tool_calls_count=len(tool_call_log),
    )
    
    yield {
        "type": "complete",
        "final_text": final_text,
        "rounds": round_i,
        "tool_calls": tool_call_log,
        "duration_ms": duration_ms,
        "stop_reason": last_stop,
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
    }
