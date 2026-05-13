"""
================================================================================
文件：tradenest/llm/base.py
作用：LLM Provider 抽象基类 + 通用数据模型
================================================================================

【核心业务功能】
定义 TradeNest 所有 LLM Provider 必须实现的统一接口。

不论底层是 Anthropic / OpenAI / DeepSeek / 公司内部网关，
上层 Agent 代码只通过这个接口调用，做到"换 Provider 不改业务代码"。

【设计要点】
1. **统一消息格式**（Message）—— 不论 Anthropic 还是 OpenAI，都先转成这个格式
2. **统一工具调用格式**（ToolUseBlock / ToolDefinition）
3. **统一响应格式**（LLMResponse）—— 含 stop_reason，能区分"要调工具"vs"完成"
4. **流式输出**（chat_stream）—— 用 async generator 吐 token

【为什么不直接用 Anthropic SDK / OpenAI SDK】
- 它们的格式有细微差异（如 OpenAI 的 tool_calls vs Anthropic 的 tool_use）
- 我们要在 Agent loop 里统一处理
- 留扩展空间（未来加新 Provider 不用改 Agent）

================================================================================
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal


# ============================================================
# 数据模型（Provider 间通用）
# ============================================================

@dataclass
class Message:
    """通用对话消息。
    
    支持的 role:
    - "system":    系统提示（仅作为 LLM 的 system 字段，不进 messages 列表）
    - "user":      用户消息
    - "assistant": AI 回复
    - "tool":      工具结果（Anthropic 的 tool_result，OpenAI 的 tool role）
    """
    role: Literal["system", "user", "assistant", "tool"]
    """消息角色"""
    
    content: str | list[dict[str, Any]]
    """消息内容。简单场景是 str；含工具调用时是 list[block]"""
    
    # 仅 tool 角色用：对应的 tool_use_id
    tool_use_id: str | None = None
    
    # 仅 assistant 角色用：模型可能返回多个工具调用
    tool_uses: list["ToolUseBlock"] = field(default_factory=list)


@dataclass
class ToolDefinition:
    """工具定义（给 LLM 看的元数据）。
    
    LLM 根据这个判断"什么时候调用、传什么参数"。
    """
    name: str
    """工具名，如 'get_realtime_quote'"""
    
    description: str
    """工具说明（要写清楚，否则 LLM 不会用）"""
    
    input_schema: dict[str, Any]
    """JSON Schema 描述参数。例如:
    {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "股票代码"}
        },
        "required": ["code"]
    }
    """


@dataclass
class ToolUseBlock:
    """LLM 决定调用某工具时的请求块。"""
    id: str
    """这次调用的唯一 ID。后续返回结果时要带上"""
    
    name: str
    """要调用的工具名"""
    
    input: dict[str, Any]
    """LLM 提取的参数（结构化的）"""


@dataclass
class TextBlock:
    """LLM 输出的文本块。"""
    text: str


@dataclass
class LLMResponse:
    """LLM 一次完整响应。
    
    Agent loop 根据 stop_reason 判断接下来怎么走：
    - "tool_use": 模型想调工具 → 执行工具 → 把结果回填进 messages → 再调 LLM
    - "end_turn": 模型已经回答完了 → 跳出循环
    - "max_tokens": 超长被截断 → 报错或继续
    """
    content: list[TextBlock | ToolUseBlock]
    """响应内容（可能含多段文本 + 多个工具调用请求）"""
    
    stop_reason: str
    """停止原因：tool_use / end_turn / max_tokens / refusal"""
    
    model: str
    """实际响应的模型名"""
    
    usage: dict[str, int] = field(default_factory=dict)
    """token 用量：{"input_tokens": int, "output_tokens": int}"""
    
    raw: dict[str, Any] | None = None
    """底层 SDK 原始响应（调试用）"""


# ============================================================
# Provider 抽象基类
# ============================================================

class LLMProvider(ABC):
    """LLM Provider 接口。
    
    每个具体实现（Anthropic / OpenAI / 内部网关）都要继承此类。
    """
    
    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Provider 唯一 ID"""
        ...
    
    @property
    @abstractmethod
    def default_model(self) -> str:
        """该 Provider 默认模型"""
        ...
    
    @abstractmethod
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
        """非流式对话调用。
        
        返回一次完整的 LLMResponse。
        
        Args:
            messages: 对话历史
            system: 系统提示
            tools: 可用工具列表
            model: 指定模型（None 用 default_model）
            max_tokens: 最大输出长度
            temperature: 0-1，None 用默认
        """
        ...
    
    @abstractmethod
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
        """流式对话调用。
        
        Yields:
            事件 dict：
            - {"type": "text_delta", "text": "..."}: 流式文本
            - {"type": "tool_use_start", "tool_use": ToolUseBlock}: 开始调用工具
            - {"type": "tool_use_input", "id": "...", "json_partial": "..."}: 工具参数流式
            - {"type": "message_stop", "stop_reason": "...", "usage": {...}}: 完成
        """
        ...
    
    async def health_check(self) -> bool:
        """简单 ping 测试 Provider 是否可用。
        
        默认实现：发一条最简单的消息，看能不能回。
        子类可以重写得更轻量（如调 /health 接口）。
        """
        try:
            resp = await self.chat(
                messages=[Message(role="user", content="ping")],
                max_tokens=10,
            )
            return resp is not None
        except Exception:
            return False
