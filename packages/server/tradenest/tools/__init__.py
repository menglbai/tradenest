"""
TradeNest 工具系统

工具（Tool）= Agent 可以调用的"能力函数"。
LLM 看到工具定义后会决定何时调用 + 传什么参数。

子模块：
- base.py:    Tool 抽象 + 注册器
- market.py:  实时行情 / K 线 / 基本信息（基于 AkShare）
- news.py:    新闻 / 公告（基于 AkShare）
- mock.py:    Mock 数据（无网络时兜底）
"""

from tradenest.tools.base import (
    Tool,
    ToolResult,
    register_tool,
    get_all_tools,
    get_tool_by_name,
    get_tool_definitions,
    execute_tool,
)

# 自动 import 子模块，触发 @register_tool 装饰器执行（注册到全局表）
# 注意：新增工具模块必须在这里 import，否则装饰器不会执行，工具不会注册
from tradenest.tools import market, news, mock, macro, sector  # noqa: F401

__all__ = [
    "Tool",
    "ToolResult",
    "register_tool",
    "get_all_tools",
    "get_tool_by_name",
    "get_tool_definitions",
    "execute_tool",
]
