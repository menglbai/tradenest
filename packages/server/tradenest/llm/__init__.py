"""
TradeNest LLM Provider 抽象层

把不同厂商 / 不同协议的大模型 API 抽象成一个统一接口，
让上层 Agent 代码不关心"用谁的 API"。

子模块：
- base.py:                LLMProvider 基类 + 数据模型
- anthropic_provider.py:  Anthropic API 协议实现（含小红书内部网关）
- openai_compat_provider: OpenAI 兼容协议实现（DeepSeek/Qwen/GPT）
- router.py:              按任务类型选 Provider + 模型
- registry.py:            Provider 注册中心（单例）
"""

from tradenest.llm.base import (
    LLMProvider,
    Message,
    ToolDefinition,
    ToolUseBlock,
    LLMResponse,
)
from tradenest.llm.registry import get_registry

__all__ = [
    "LLMProvider",
    "Message",
    "ToolDefinition",
    "ToolUseBlock",
    "LLMResponse",
    "get_registry",
]
