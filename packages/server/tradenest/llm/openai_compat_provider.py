"""
================================================================================
文件：tradenest/llm/openai_compat_provider.py
作用：OpenAI 兼容协议 Provider（用于 DeepSeek / Qwen / GPT 等）
================================================================================

【核心业务功能】
实现 OpenAI Chat Completions API 协议（含 tools / tool_calls 子协议）。

支持市面上**所有声称兼容 OpenAI 协议的模型 API**：
- OpenAI（gpt-4o / gpt-5 等）
- DeepSeek（deepseek-chat / deepseek-coder）
- Qwen（通过 DashScope OpenAI 兼容模式）
- 公司内部自建网关（只要协议兼容）

【与 Anthropic 协议的差异】
- OpenAI: messages role 含 "system"（不分顶层）
- OpenAI: tool 调用是 assistant.tool_calls[]
- OpenAI: tool 结果是 role=tool / tool_call_id=xxx
- 流式格式不同（chunked + delta）

================================================================================
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from tradenest.core.config import ProviderConfig
from tradenest.core.logging import get_logger
from tradenest.llm.base import (
    LLMProvider,
    LLMResponse,
    Message,
    TextBlock,
    ToolDefinition,
    ToolUseBlock,
)

log = get_logger("tradenest.llm.openai_compat")


class OpenAICompatProvider(LLMProvider):
    """OpenAI 协议 Provider（DeepSeek / Qwen / GPT 通用）。"""
    
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"),
            headers=config.headers,
            timeout=httpx.Timeout(config.timeout_seconds),
            trust_env=True,
        )
    
    @property
    def provider_id(self) -> str:
        return self.config.id
    
    @property
    def default_model(self) -> str:
        return self.config.default_model
    
    async def aclose(self) -> None:
        await self._client.aclose()
    
    @staticmethod
    def _convert_messages(messages: list[Message]) -> list[dict[str, Any]]:
        """转换消息为 OpenAI 格式。"""
        api_messages: list[dict[str, Any]] = []
        
        for msg in messages:
            if msg.role == "tool":
                # OpenAI: role=tool, tool_call_id=xxx, content=结果
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_use_id or "",
                    "content": msg.content if isinstance(msg.content, str) else json.dumps(msg.content),
                })
                continue
            
            if msg.role == "assistant" and msg.tool_uses:
                # assistant 含工具调用
                tool_calls = [
                    {
                        "id": tu.id,
                        "type": "function",
                        "function": {
                            "name": tu.name,
                            "arguments": json.dumps(tu.input, ensure_ascii=False),
                        },
                    }
                    for tu in msg.tool_uses
                ]
                api_messages.append({
                    "role": "assistant",
                    "content": msg.content if isinstance(msg.content, str) else "",
                    "tool_calls": tool_calls,
                })
                continue
            
            # 普通 user / assistant / system
            api_messages.append({"role": msg.role, "content": msg.content})
        
        return api_messages
    
    @staticmethod
    def _convert_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        """转换工具定义为 OpenAI 格式（function calling）。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]
    
    @staticmethod
    def _parse_response(data: dict[str, Any]) -> LLMResponse:
        """解析 /v1/chat/completions 响应。"""
        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        
        blocks: list[TextBlock | ToolUseBlock] = []
        
        # 文本部分
        text = msg.get("content")
        if text:
            blocks.append(TextBlock(text=text))
        
        # 工具调用
        for tc in msg.get("tool_calls", []) or []:
            fn = tc.get("function", {})
            try:
                tool_input = json.loads(fn.get("arguments", "{}"))
            except json.JSONDecodeError:
                tool_input = {}
            blocks.append(ToolUseBlock(
                id=tc.get("id", ""),
                name=fn.get("name", ""),
                input=tool_input,
            ))
        
        # OpenAI 的 finish_reason 映射到我们的 stop_reason
        finish = choice.get("finish_reason", "stop")
        stop_reason_map = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
        }
        stop_reason = stop_reason_map.get(finish, finish)
        
        usage_raw = data.get("usage", {})
        return LLMResponse(
            content=blocks,
            stop_reason=stop_reason,
            model=data.get("model", ""),
            usage={
                "input_tokens": usage_raw.get("prompt_tokens", 0),
                "output_tokens": usage_raw.get("completion_tokens", 0),
            },
            raw=data,
        )
    
    async def chat(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """非流式调用。"""
        # 把 system prompt 注入到 messages 头部
        msgs_with_system: list[Message] = list(messages)
        if system:
            msgs_with_system.insert(0, Message(role="system", content=system))
        
        api_messages = self._convert_messages(msgs_with_system)
        
        body: dict[str, Any] = {
            "model": model or self.config.default_model,
            "messages": api_messages,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = self._convert_tools(tools)
        if temperature is not None:
            body["temperature"] = temperature
        
        log.info(
            "llm_request",
            provider=self.config.id,
            model=body["model"],
            messages_count=len(api_messages),
            has_tools=bool(tools),
        )
        
        try:
            response = await self._client.post("/chat/completions", json=body)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            log.error(
                "llm_http_error",
                provider=self.config.id,
                status=e.response.status_code,
                body=e.response.text[:500],
            )
            raise
        
        return self._parse_response(response.json())
    
    async def chat_stream(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式 SSE 调用。"""
        msgs_with_system: list[Message] = list(messages)
        if system:
            msgs_with_system.insert(0, Message(role="system", content=system))
        
        api_messages = self._convert_messages(msgs_with_system)
        
        body: dict[str, Any] = {
            "model": model or self.config.default_model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            body["tools"] = self._convert_tools(tools)
        if temperature is not None:
            body["temperature"] = temperature
        
        log.info("llm_stream_start", provider=self.config.id, model=body["model"])
        
        # 缓冲工具调用（OpenAI 把 arguments 流式返回）
        # tc_index → {id, name, arguments_buffer}
        tool_buffers: dict[int, dict[str, Any]] = {}
        
        async with self._client.stream("POST", "/chat/completions", json=body) as resp:
            resp.raise_for_status()
            
            async for raw_line in resp.aiter_lines():
                line = raw_line.strip()
                if not line.startswith("data:"):
                    continue
                
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                
                choice = chunk.get("choices", [{}])[0]
                delta = choice.get("delta", {})
                
                # 文本流
                if delta.get("content"):
                    yield {"type": "text_delta", "text": delta["content"]}
                
                # 工具调用流
                for tc in delta.get("tool_calls", []) or []:
                    idx = tc.get("index", 0)
                    
                    # 第一次出现这个 tool_call → 初始化 buffer
                    if idx not in tool_buffers:
                        tool_buffers[idx] = {
                            "id": tc.get("id", ""),
                            "name": (tc.get("function", {}) or {}).get("name", ""),
                            "arguments": "",
                        }
                        yield {
                            "type": "tool_use_start",
                            "tool_use": ToolUseBlock(
                                id=tool_buffers[idx]["id"],
                                name=tool_buffers[idx]["name"],
                                input={},
                            ),
                        }
                    
                    # 累积 arguments
                    fn = tc.get("function", {}) or {}
                    if fn.get("arguments"):
                        tool_buffers[idx]["arguments"] += fn["arguments"]
                        yield {
                            "type": "tool_use_input",
                            "id": tool_buffers[idx]["id"],
                            "json_partial": fn["arguments"],
                        }
                
                # finish_reason
                finish = choice.get("finish_reason")
                if finish:
                    # 把所有缓冲的 tool_call 解析成完整 input
                    for buf in tool_buffers.values():
                        try:
                            parsed = json.loads(buf["arguments"]) if buf["arguments"] else {}
                        except json.JSONDecodeError:
                            parsed = {}
                        yield {
                            "type": "tool_use_complete",
                            "tool_use": ToolUseBlock(
                                id=buf["id"],
                                name=buf["name"],
                                input=parsed,
                            ),
                        }
                    
                    stop_reason_map = {
                        "stop": "end_turn",
                        "tool_calls": "tool_use",
                        "length": "max_tokens",
                    }
                    yield {
                        "type": "message_stop",
                        "stop_reason": stop_reason_map.get(finish, finish),
                        "usage": chunk.get("usage", {}) or {},
                    }
        
        log.info("llm_stream_end", provider=self.config.id)
