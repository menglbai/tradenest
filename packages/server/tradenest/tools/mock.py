"""
================================================================================
文件：tradenest/tools/mock.py
作用：Mock 工具（用于离线开发 / 演示）
================================================================================

【核心业务功能】
当真实数据源不可用时（容器无外网、AkShare 接口挂了、等），
Mock 工具提供假数据让 Agent loop 仍能跑通。

主要用途：
- 容器环境开发（容器不能直连东财）
- 单元测试 / 烟雾测试
- 演示场景

注意：**生产环境用户机器跑时，应禁用 Mock 工具**（避免 LLM 误用 mock 数据）。
通过 settings.disable_mock_tools = True 关闭。

================================================================================
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from tradenest.core.logging import get_logger
from tradenest.tools.base import ToolResult, register_tool

log = get_logger("tradenest.tools.mock")


# 几个常见股票的 mock 数据
_MOCK_STOCKS = {
    "600519": {"name": "贵州茅台", "price": 1681.50, "change_pct": 0.85},
    "000001": {"name": "平安银行", "price": 11.23, "change_pct": -0.42},
    "300750": {"name": "宁德时代", "price": 198.30, "change_pct": 1.20},
    "601318": {"name": "中国平安", "price": 51.20, "change_pct": 0.65},
    "000333": {"name": "美的集团", "price": 67.80, "change_pct": -0.30},
    "601899": {"name": "紫金矿业", "price": 19.80, "change_pct": 2.15},
    "002594": {"name": "比亚迪", "price": 285.40, "change_pct": 1.80},
}


@register_tool(
    name="mock_get_stock_demo",
    description=(
        "【仅 demo / 测试用】获取 mock 股票数据。"
        "正常用户场景应该用 get_realtime_quote。"
        "此工具用于演示 Agent loop 时不依赖外网。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "股票代码"},
        },
        "required": ["code"],
    },
)
async def mock_get_stock_demo(code: str) -> ToolResult:
    """Mock 行情数据（演示用）。"""
    info = _MOCK_STOCKS.get(code)
    if info is None:
        # 给个随机 mock
        info = {
            "name": f"未知股票{code}",
            "price": round(random.uniform(10, 500), 2),
            "change_pct": round(random.uniform(-3, 3), 2),
        }
    
    content = (
        f"⚠️ 这是 Mock 数据（演示用，非真实行情）\n"
        f"\n"
        f"【{info['name']} ({code})】:\n"
        f"  最新价: ¥{info['price']}\n"
        f"  涨跌幅: {info['change_pct']:+.2f}%\n"
        f"  数据时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"  数据来源: TradeNest Mock"
    )
    return ToolResult(content=content, metadata={"mock": True, **info})
