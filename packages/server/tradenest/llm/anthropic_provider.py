"""
================================================================================
文件：tradenest/llm/anthropic_provider.py
作用：Anthropic 协议 Provider 实现（公网 + 内部网关都用这个）
================================================================================

【核心业务功能】
实现 Anthropic Messages API 协议的 LLM Provider。

支持两种部署：
1. **公网 Anthropic API**（api.anthropic.com）
2. **小红书内部 codewiz 网关**（codewiz.devops.xiaohongshu.com/llmadapter/anthropic）

两者的 API 协议**完全一致**（都是 Anthropic 的 /v1/messages 接口），
区别只在 base_url 和 headers——所以用同一个 Provider 实现。

【代理能力】
通过 ProviderConfig.base_url + headers 配置实现"应用层代理"：
- 想走公网：base_url = https://api.anthropic.com
- 想走内部：base_url = http://codewiz... + 加 x-adapter-* headers
- 想走第三方代理（如 xhs-llm-proxy 在 18790 端口）：base_url = http://localhost:18790

【为什么不直接用 anthropic SDK】
官方 SDK 对 base_url 支持有限（不能自由换协议），且不容易加自定义 headers。
我们用 httpx 直接调 HTTP API，灵活度最高。

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

log = get_logger("tradenest.llm.anthropic")


class AnthropicProvider(LLMProvider):
    """通用 Anthropic 协议 Provider。
    
    用法：
        cfg = ProviderConfig(
            id="internal-xhs",
            kind="anthropic",
            base_url="http://codewiz.devops.xiaohongshu.com/llmadapter/anthropic",
            api_key="...",
            default_model="claude-4.6-sonnet-google",
            headers={"x-adapter-api-key": "...", ...},
        )
        provider = AnthropicProvider(cfg)
        resp = await provider.chat([Message(role="user", content="hi")])
    """
    
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        # 创建 httpx client（保持长连接，复用更高效）
        # 关键：trust_env=False 避免误用环境变量里的代理
        # 但要给内部网关让路：如果 base_url 是 .xiaohongshu.com，不走代理
        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"),
            headers=config.headers,
            timeout=httpx.Timeout(config.timeout_seconds),
            trust_env=True,  # 仍允许从 env 读 NO_PROXY 配置
        )
    
    @property
    def provider_id(self) -> str:
        return self.config.id
    
    @property
    def default_model(self) -> str:
        return self.config.default_model
    
    async def aclose(self) -> None:
        """关闭 httpx client。在应用 shutdown 时调用。"""
        await self._client.aclose()
    
    # ============================================================
    # 内部：把通用 Message 转成 Anthropic API 格式
    # ============================================================
    
    @staticmethod
    def _convert_messages(messages: list[Message]) -> tuple[list[dict[str, Any]], str | None]:
        """转换消息格式 + 抽取 system。
        
        Returns:
            (messages_for_api, system_prompt)
        """
        api_messages: list[dict[str, Any]] = []
        system_prompt: str | None = None
        
        for msg in messages:
            if msg.role == "system":
                # Anthropic API 的 system 是顶层字段，不进 messages
                if isinstance(msg.content, str):
                    system_prompt = msg.content
                continue
            
            if msg.role == "tool":
                # tool 结果要放在 user 角色的 content[].type=tool_result
                api_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.tool_use_id or "",
                        "content": msg.content if isinstance(msg.content, str) else json.dumps(msg.content),
                    }],
                })
                continue
            
            if msg.role == "assistant":
                # assistant 消息可能含工具调用块
                if msg.tool_uses:
                    blocks: list[dict[str, Any]] = []
                    if isinstance(msg.content, str) and msg.content:
                        blocks.append({"type": "text", "text": msg.content})
                    for tu in msg.tool_uses:
                        blocks.append({
                            "type": "tool_use",
                            "id": tu.id,
                            "name": tu.name,
                            "input": tu.input,
                        })
                    api_messages.append({"role": "assistant", "content": blocks})
                else:
                    # 纯文本回复
                    api_messages.append({"role": "assistant", "content": msg.content})
                continue
            
            # user 消息
            api_messages.append({"role": "user", "content": msg.content})
        
        return api_messages, system_prompt
    
    @staticmethod
    def _convert_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        """转换工具定义为 Anthropic 格式。"""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]
    
    @staticmethod
    def _parse_response(data: dict[str, Any]) -> LLMResponse:
        """解析 Anthropic /v1/messages 响应。"""
        blocks: list[TextBlock | ToolUseBlock] = []
        for b in data.get("content", []):
            if b.get("type") == "text":
                blocks.append(TextBlock(text=b.get("text", "")))
            elif b.get("type") == "tool_use":
                blocks.append(ToolUseBlock(
                    id=b.get("id", ""),
                    name=b.get("name", ""),
                    input=b.get("input", {}),
                ))
        
        return LLMResponse(
            content=blocks,
            stop_reason=data.get("stop_reason", "unknown"),
            model=data.get("model", ""),
            usage=data.get("usage", {}),
            raw=data,
        )
    
    # ============================================================
    # 主接口：chat / chat_stream
    # ============================================================
    
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
        """非流式调用 /v1/messages。"""
        api_messages, sys_from_msgs = self._convert_messages(messages)
        final_system = system or sys_from_msgs
        
        body: dict[str, Any] = {
            "model": model or self.config.default_model,
            "max_tokens": max_tokens,
            "messages": api_messages,
        }
        if final_system:
            body["system"] = final_system
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
            response = await self._client.post("/v1/messages", json=body)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            log.error(
                "llm_http_error",
                provider=self.config.id,
                status=e.response.status_code,
                body=e.response.text[:500],
            )
            raise
        
        data = response.json()
        result = self._parse_response(data)
        
        log.info(
            "llm_response",
            provider=self.config.id,
            stop_reason=result.stop_reason,
            input_tokens=result.usage.get("input_tokens", 0),
            output_tokens=result.usage.get("output_tokens", 0),
        )
        return result
    
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
        """流式 SSE 调用 /v1/messages。"""
        api_messages, sys_from_msgs = self._convert_messages(messages)
        final_system = system or sys_from_msgs
        
        body: dict[str, Any] = {
            "model": model or self.config.default_model,
            "max_tokens": max_tokens,
            "messages": api_messages,
            "stream": True,
        }
        if final_system:
            body["system"] = final_system
        if tools:
            body["tools"] = self._convert_tools(tools)
        if temperature is not None:
            body["temperature"] = temperature
        
        log.info("llm_stream_start", provider=self.config.id, model=body["model"])
        
        # 缓冲：tool_use 的 input JSON 是逐 token 流的，要拼起来
        # tool_use_id → {name, partial_json}
        tool_buffers: dict[str, dict[str, Any]] = {}
        # block index → tool_use_id（SSE event 用 index 引用块）
        index_to_tool_id: dict[int, str] = {}
        
        async with self._client.stream("POST", "/v1/messages", json=body) as resp:
            resp.raise_for_status()
            
            # SSE 解析：每行 "event: xxx\ndata: {...}\n\n"
            event_type: str | None = None
            async for raw_line in resp.aiter_lines():
                line = raw_line.strip()
                if not line:
                    event_type = None
                    continue
                
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                    continue
                
                if line.startswith("data:"):
                    data_str = line[5:].strip()
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    
                    # 根据 SSE 事件类型转换为统一事件
                    if event_type == "content_block_start":
                        block = data.get("content_block", {})
                        idx = data.get("index", 0)
                        if block.get("type") == "text":
                            yield {"type": "text_block_start", "index": idx}
                        elif block.get("type") == "tool_use":
                            tu_id = block.get("id", "")
                            tool_buffers[tu_id] = {
                                "name": block.get("name", ""),
                                "input_json": "",
                            }
                            index_to_tool_id[idx] = tu_id
                            yield {
                                "type": "tool_use_start",
                                "tool_use": ToolUseBlock(
                                    id=tu_id,
                                    name=block.get("name", ""),
                                    input={},
                                ),
                            }
                    
                    elif event_type == "content_block_delta":
                        delta = data.get("delta", {})
                        idx = data.get("index", 0)
                        if delta.get("type") == "text_delta":
                            yield {"type": "text_delta", "text": delta.get("text", "")}
                        elif delta.get("type") == "input_json_delta":
                            tu_id = index_to_tool_id.get(idx)
                            if tu_id and tu_id in tool_buffers:
                                tool_buffers[tu_id]["input_json"] += delta.get("partial_json", "")
                                yield {
                                    "type": "tool_use_input",
                                    "id": tu_id,
                                    "json_partial": delta.get("partial_json", ""),
                                }
                    
                    elif event_type == "content_block_stop":
                        idx = data.get("index", 0)
                        tu_id = index_to_tool_id.get(idx)
                        if tu_id and tu_id in tool_buffers:
                            # 完成一个 tool_use：把 partial_json 解析成完整 input
                            buf = tool_buffers[tu_id]
                            try:
                                parsed_input = json.loads(buf["input_json"]) if buf["input_json"] else {}
                            except json.JSONDecodeError:
                                parsed_input = {}
                            yield {
                                "type": "tool_use_complete",
                                "tool_use": ToolUseBlock(
                                    id=tu_id,
                                    name=buf["name"],
                                    input=parsed_input,
                                ),
                            }
                    
                    elif event_type == "message_delta":
                        delta = data.get("delta", {})
                        if "stop_reason" in delta:
                            yield {
                                "type": "message_stop",
                                "stop_reason": delta.get("stop_reason"),
                                "usage": data.get("usage", {}),
                            }
                    
                    elif event_type == "message_stop":
                        # 最终结束信号
                        pass
        
        log.info("llm_stream_end", provider=self.config.id)
