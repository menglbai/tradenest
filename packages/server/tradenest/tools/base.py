"""
================================================================================
文件：tradenest/tools/base.py
作用：工具抽象 + 注册装饰器 + 调用分发
================================================================================

【核心业务功能】
1. 定义 Tool 数据结构（含 LLM 可见的元信息 + 执行函数）
2. 提供 @register_tool 装饰器：模块加载时自动注册到全局表
3. 提供 execute_tool() 异步分发：Agent loop 拿到 LLM 返回的 tool_use 后调它

【设计要点】
- Tool 用 dataclass 表示，把 ToolDefinition（给 LLM 看的）和 handler（实际执行的）打包
- 装饰器风格让"加新工具"非常简单：写一个函数 + 加 @register_tool
- 异步执行（所有工具都 async def），方便 IO 密集型工具（HTTP / 数据库）

【典型新增工具流程】
    @register_tool(
        name="get_realtime_quote",
        description="获取 A 股实时行情",
        input_schema={"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
    )
    async def get_realtime_quote(code: str) -> ToolResult:
        ...
        return ToolResult(content="...")

================================================================================
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from tradenest.core.logging import get_logger
from tradenest.llm.base import ToolDefinition

log = get_logger("tradenest.tools")


# ============================================================
# 数据模型
# ============================================================

@dataclass
class ToolResult:
    """工具执行结果。
    
    会被回传给 LLM 作为下一轮输入。
    """
    content: str
    """文本结果（必须是 str，因为 LLM 看 tool_result 是字符串）"""
    
    is_error: bool = False
    """是否错误结果（LLM 看到 is_error 会知道工具挂了）"""
    
    metadata: dict[str, Any] = field(default_factory=dict)
    """额外元数据（用于日志 / trace，不传给 LLM）"""


# Handler 签名：async (**kwargs) -> ToolResult | str
ToolHandler = Callable[..., Awaitable[Any]]


@dataclass
class Tool:
    """完整工具定义。"""
    
    definition: ToolDefinition
    """LLM 可见的元信息"""
    
    handler: ToolHandler
    """实际执行的异步函数"""


# ============================================================
# 全局注册表
# ============================================================

_REGISTRY: dict[str, Tool] = {}


def register_tool(
    *,
    name: str,
    description: str,
    input_schema: dict[str, Any],
) -> Callable[[ToolHandler], ToolHandler]:
    """装饰器：注册工具到全局表。
    
    Args:
        name: 工具名（必须在全项目唯一）
        description: 工具描述（写给 LLM 看的，要清楚）
        input_schema: 参数 JSON Schema
    
    Example:
        @register_tool(
            name="get_realtime_quote",
            description="获取 A 股实时行情",
            input_schema={
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
        )
        async def get_realtime_quote(code: str) -> ToolResult:
            ...
    """
    def decorator(fn: ToolHandler) -> ToolHandler:
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"工具 {name} 必须是 async function（用 async def）")
        
        if name in _REGISTRY:
            log.warning("tool_overridden", name=name)
        
        _REGISTRY[name] = Tool(
            definition=ToolDefinition(
                name=name,
                description=description,
                input_schema=input_schema,
            ),
            handler=fn,
        )
        log.debug("tool_registered", name=name)
        return fn
    
    return decorator


def get_all_tools() -> list[Tool]:
    """获取所有已注册工具"""
    return list(_REGISTRY.values())


def get_tool_by_name(name: str) -> Tool | None:
    """按名称查工具"""
    return _REGISTRY.get(name)


def get_tool_definitions() -> list[ToolDefinition]:
    """获取所有工具的 LLM 可见定义（喂给 chat() 的 tools 参数）"""
    return [t.definition for t in _REGISTRY.values()]


# ============================================================
# 调用分发
# ============================================================

async def execute_tool(name: str, arguments: dict[str, Any]) -> ToolResult:
    """执行某个工具。
    
    Args:
        name: 工具名
        arguments: LLM 提取的参数（dict）
    
    Returns:
        ToolResult
    """
    tool = get_tool_by_name(name)
    if tool is None:
        log.warning("tool_not_found", name=name)
        return ToolResult(
            content=f"工具 '{name}' 未注册。可用工具：{list(_REGISTRY.keys())}",
            is_error=True,
        )
    
    log.info("tool_executing", name=name, arguments=arguments)
    try:
        # 调用 handler，捕获返回
        result = await tool.handler(**arguments)
        
        # 把返回归一化为 ToolResult
        if isinstance(result, ToolResult):
            tool_result = result
        elif isinstance(result, str):
            tool_result = ToolResult(content=result)
        elif isinstance(result, (dict, list)):
            tool_result = ToolResult(content=json.dumps(result, ensure_ascii=False, default=str))
        else:
            tool_result = ToolResult(content=str(result))
        
        log.info(
            "tool_executed",
            name=name,
            is_error=tool_result.is_error,
            content_len=len(tool_result.content),
        )
        return tool_result
    
    except Exception as e:
        log.error("tool_execution_failed", name=name, error=str(e), exc_info=e)
        return ToolResult(
            content=f"工具 '{name}' 执行失败：{type(e).__name__}: {e}",
            is_error=True,
        )
