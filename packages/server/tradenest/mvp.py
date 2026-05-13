"""TradeNest Day 1 MVP - 最小可跑的 Agent loop。

目的：验证 Anthropic Agent SDK + 工具调用能跑通。

跑法:
    cd packages/server
    uv run python -m tradenest.mvp

输出：用 Claude 分析一只股票（贵州茅台 600519），调用一个简单的"获取行情"工具。
"""

import asyncio
import os
import sys
from typing import Any

from anthropic import AsyncAnthropic


# ============================================================
# 工具定义 - Day 1 只放一个最简单的"模拟行情"工具
# 后面 Day 2-7 会接入真实 AkShare 数据源
# ============================================================

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_stock_price",
        "description": (
            "获取指定 A 股股票的当前价格信息。"
            "Day 1 是 mock 数据，Day 2 会接入 AkShare 真实数据。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "A 股代码，如 600519（贵州茅台）",
                },
            },
            "required": ["code"],
        },
    },
]


async def get_stock_price_mock(code: str) -> dict[str, Any]:
    """Mock 行情数据 - Day 2 替换为 AkShare。"""
    mock_data = {
        "600519": {"name": "贵州茅台", "price": 1681.50, "change_pct": 0.85},
        "000001": {"name": "平安银行", "price": 11.23, "change_pct": -0.42},
        "300750": {"name": "宁德时代", "price": 198.30, "change_pct": 1.20},
    }
    return mock_data.get(code, {"name": "未知", "price": 0, "change_pct": 0})


async def execute_tool(name: str, args: dict[str, Any]) -> str:
    """工具调用分发器。"""
    if name == "get_stock_price":
        result = await get_stock_price_mock(args["code"])
        return (
            f"股票 {args['code']} ({result['name']}): "
            f"当前价 ¥{result['price']}, 涨跌幅 {result['change_pct']:+.2f}%"
        )
    return f"未知工具: {name}"


# ============================================================
# Agent loop - 用 Anthropic 原生 SDK
# 后面 Day 5+ 升级为 LangGraph 复杂状态机
# ============================================================


async def run_agent(user_message: str) -> None:
    """简化的 Agent loop。"""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ 请在 .env 中配置 ANTHROPIC_API_KEY")
        sys.exit(1)

    client = AsyncAnthropic(api_key=api_key)

    print(f"🪺  TradeNest Day 1 MVP")
    print(f"📝 用户问: {user_message}\n")

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]

    # 最多 5 轮工具调用循环
    for turn in range(5):
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",  # Day 1 用 sonnet，后期支持模型切换
            max_tokens=4096,
            tools=TOOLS,  # type: ignore[arg-type]
            messages=messages,  # type: ignore[arg-type]
            system=(
                "你是 TradeNest——一个长期陪伴用户做投资研究的 AI 伙伴。"
                "你不给具体买卖建议，不预测股价，不替用户决策。"
                "你帮用户：整理信息、多视角分析、跨时间反馈、苏格拉底式反问。"
                "你必须遵守中国金融监管要求，绝不输出'建议买入/卖出'等表述。"
            ),
        )

        # 检查是否需要调用工具
        if response.stop_reason == "tool_use":
            print(f"🔧 第 {turn + 1} 轮: 模型决定调用工具")

            # 收集所有工具调用
            tool_results = []
            for block in response.content:
                if block.type == "text":
                    print(f"💭 模型思考: {block.text[:200]}")
                elif block.type == "tool_use":
                    print(f"   → 调用 {block.name}({block.input})")
                    result = await execute_tool(block.name, block.input)  # type: ignore[arg-type]
                    print(f"   ← 工具返回: {result}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            # 把工具结果回填给模型
            messages.append({"role": "assistant", "content": response.content})  # type: ignore[arg-type]
            messages.append({"role": "user", "content": tool_results})  # type: ignore[arg-type]

        elif response.stop_reason == "end_turn":
            print(f"\n✅ 第 {turn + 1} 轮: 模型完成回答\n")
            print("=" * 60)
            for block in response.content:
                if block.type == "text":
                    print(block.text)
            print("=" * 60)
            return
        else:
            print(f"⚠️ 未知 stop_reason: {response.stop_reason}")
            return

    print("⚠️ 达到最大循环次数")


async def main() -> None:
    # Day 1 默认问一个问题，验证 Agent + 工具 + Claude 能跑通
    question = "贵州茅台现在多少钱？基于这个价格，从估值角度做个简单的多视角分析（不要给买卖建议）。"
    await run_agent(question)


if __name__ == "__main__":
    asyncio.run(main())
