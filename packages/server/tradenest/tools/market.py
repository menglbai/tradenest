"""
================================================================================
文件：tradenest/tools/market.py
作用：实时行情 / K 线 / 基本信息 / 资金流向工具
================================================================================

【数据源策略】
- 实时行情：腾讯行情接口（qt.gtimg.cn）—— 免费、无需 key、不反爬、容器和本机均可用
- 历史K线：AkShare 东财接口（需要能访问东财，本机一般可用）
- 基本面：AkShare 东财接口
- 资金流向：AkShare 东财接口

================================================================================
"""

from __future__ import annotations

import os
from typing import Any

from tradenest.core.logging import get_logger
from tradenest.tools.base import ToolResult, register_tool

log = get_logger("tradenest.tools.market")


# ============================================================
# 腾讯行情接口
# ============================================================
# 返回格式: v_sh600519="1~贵州茅台~600519~价格~昨收~今开~成交量(手)~...~涨跌额(31)~涨跌幅%(32)~最高(33)~最低(34)~...~成交额万(37)~换手率%(38)~..."

_TENCENT_IDX = {
    "name":       1,
    "code":       2,
    "price":      3,
    "prev_close": 4,
    "open":       5,
    "volume":     6,    # 成交量（手）
    "change":     31,   # 涨跌额
    "change_pct": 32,   # 涨跌幅%
    "high":       33,
    "low":        34,
    "amount":     37,   # 成交额（万元）
    "turnover":   38,   # 换手率%
}


def _code_to_prefix(code: str) -> str:
    """把裸代码转成腾讯格式前缀：sh600519 / sz000858。"""
    if code.startswith(("sh", "sz", "hk")):
        return code
    if code.startswith("6"):
        return "sh" + code
    if code.startswith(("0", "3")):
        return "sz" + code
    return "sh" + code  # 默认沪市


def _parse_tencent_line(line: str) -> dict | None:
    """解析腾讯行情单行。"""
    try:
        inner = line.split('"')[1]
        parts = inner.split("~")
        result = {}
        for key, idx in _TENCENT_IDX.items():
            result[key] = parts[idx].strip() if idx < len(parts) else ""
        return result if result.get("code") else None
    except Exception:
        return None


def _fetch_tencent(codes: list[str]) -> dict[str, dict]:
    """批量拉腾讯行情，返回 {裸code: {...}}。"""
    import requests
    prefixed = [_code_to_prefix(c) for c in codes]
    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
    r = requests.get(url, timeout=8, headers={"Referer": "https://gu.qq.com/"})
    result: dict[str, dict] = {}
    for line in r.text.strip().split("\n"):
        if "~" not in line:
            continue
        row = _parse_tencent_line(line)
        if row and row.get("code"):
            result[row["code"]] = row
    return result


# ============================================================
# 工具 1：实时行情（腾讯接口）
# ============================================================

@register_tool(
    name="get_realtime_quote",
    description=(
        "获取 A 股的当前实时行情。"
        "返回：最新价、涨跌幅、涨跌额、开盘、最高、最低、昨收、成交量、成交额、换手率。"
        "用户问'XX 现在多少钱 / 今天涨了多少 / 实时价格'时调用。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "A 股 6 位代码，如 600519（贵州茅台）、000858（五粮液）",
            },
        },
        "required": ["code"],
    },
)
async def get_realtime_quote(code: str) -> ToolResult:
    """获取实时行情（腾讯行情接口）。"""
    code = code.strip()
    try:
        data = _fetch_tencent([code])
        row = data.get(code)
        if not row:
            # 尝试不带前导零的代码
            row = next(iter(data.values()), None) if data else None
        if not row:
            return ToolResult(
                content=f"未找到股票代码 {code}，请确认代码正确。",
                is_error=True,
            )

        sign = "+" if not str(row["change"]).startswith("-") else ""
        content = (
            f"【{row['name']} ({code})】实时行情\n"
            f"  最新价:  ¥{row['price']}\n"
            f"  涨跌额:  {sign}{row['change']}\n"
            f"  涨跌幅:  {sign}{row['change_pct']}%\n"
            f"  今开:    ¥{row['open']}\n"
            f"  最高:    ¥{row['high']}\n"
            f"  最低:    ¥{row['low']}\n"
            f"  昨收:    ¥{row['prev_close']}\n"
            f"  成交量:  {row['volume']} 手\n"
            f"  成交额:  {row['amount']} 万元\n"
            f"  换手率:  {row['turnover']}%\n"
            f"  数据来源: 腾讯行情"
        )
        return ToolResult(content=content, metadata={"row": row})

    except Exception as e:
        log.error("realtime_quote_failed", code=code, error=str(e))
        return ToolResult(
            content=f"获取 {code} 实时行情失败：{e}",
            is_error=True,
        )


# ============================================================
# 工具 2：历史 K 线（AkShare 东财）
# ============================================================

@register_tool(
    name="get_history_kline",
    description=(
        "获取 A 股的历史 K 线数据。"
        "用于看趋势、算技术指标、对比历史走势。"
        "用户问'最近一个月走势 / 历史 K 线 / 趋势'时调用。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "A 股 6 位代码"},
            "period": {
                "type": "string",
                "enum": ["daily", "weekly", "monthly"],
                "default": "daily",
                "description": "K 线周期",
            },
            "days": {
                "type": "integer",
                "default": 30,
                "minimum": 1,
                "maximum": 365,
                "description": "返回最近 N 天",
            },
            "adjust": {
                "type": "string",
                "enum": ["", "qfq", "hfq"],
                "default": "qfq",
                "description": "复权: qfq=前复权（推荐）/ hfq=后复权 / 空=不复权",
            },
        },
        "required": ["code"],
    },
)
async def get_history_kline(
    code: str,
    period: str = "daily",
    days: int = 30,
    adjust: str = "qfq",
) -> ToolResult:
    """获取历史 K 线（AkShare 东财）。"""
    try:
        import akshare as ak
        from datetime import datetime, timedelta
    except ImportError:
        return ToolResult(content="AkShare 未安装", is_error=True)

    end_date = __import__("datetime").datetime.now().strftime("%Y%m%d")
    from datetime import datetime, timedelta
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")

    try:
        df = ak.stock_zh_a_hist(
            symbol=code, period=period,
            start_date=start_date, end_date=end_date,
            adjust=adjust,
        )
        if df is None or len(df) == 0:
            return ToolResult(content=f"未取到 {code} 的历史 K 线数据", is_error=True)

        df_recent = df.tail(days)
        rows = [f"【{code}】最近 {len(df_recent)} 个 {period} K 线（复权: {adjust or '不复权'}）:"]
        rows.append("日期         开盘      收盘      最高      最低      成交量     涨跌幅")
        rows.append("-" * 70)
        for _, r in df_recent.iterrows():
            rows.append(
                f"{r['日期']}  "
                f"{r['开盘']:8.2f}  "
                f"{r['收盘']:8.2f}  "
                f"{r['最高']:8.2f}  "
                f"{r['最低']:8.2f}  "
                f"{int(r['成交量']):>9}  "
                f"{r.get('涨跌幅', 0):+.2f}%"
            )
        return ToolResult(content="\n".join(rows), metadata={"count": len(df_recent)})

    except Exception as e:
        log.error("history_kline_failed", code=code, error=str(e))
        return ToolResult(content=f"获取 {code} K 线失败：{e}", is_error=True)


# ============================================================
# 工具 3：基本信息（AkShare 东财）
# ============================================================

@register_tool(
    name="get_basic_info",
    description=(
        "获取 A 股公司的基本信息：所属行业、市值、PE、PB、ROE、上市日期等。"
        "用户问'XX 是哪个行业 / 市值多少 / 估值如何'时调用。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "A 股 6 位代码"},
        },
        "required": ["code"],
    },
)
async def get_basic_info(code: str) -> ToolResult:
    """获取公司基本信息。"""
    try:
        import akshare as ak
    except ImportError:
        return ToolResult(content="AkShare 未安装", is_error=True)

    try:
        df = ak.stock_individual_info_em(symbol=code)
        if df is None or len(df) == 0:
            return ToolResult(content=f"未找到 {code} 的基本信息", is_error=True)

        info = dict(zip(df["item"], df["value"]))
        lines = [f"【{code}】公司基本信息:"]
        for k, v in info.items():
            lines.append(f"  {k}: {v}")
        return ToolResult(content="\n".join(lines), metadata=info)

    except Exception as e:
        log.error("basic_info_failed", code=code, error=str(e))
        return ToolResult(content=f"获取 {code} 基本信息失败：{e}", is_error=True)


# ============================================================
# 工具 4：资金流向（AkShare 东财）
# ============================================================

@register_tool(
    name="get_capital_flow",
    description=(
        "获取 A 股个股的资金流向：主力净流入 / 超大单 / 大单 / 中单 / 小单。"
        "用户问'谁在买 / 主力流向 / 资金面'时调用。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "A 股 6 位代码"},
        },
        "required": ["code"],
    },
)
async def get_capital_flow(code: str) -> ToolResult:
    """获取资金流向（AkShare 东财）。"""
    try:
        import akshare as ak
    except ImportError:
        return ToolResult(content="AkShare 未安装", is_error=True)

    try:
        market = "sh" if code.startswith("6") else "sz"
        df = ak.stock_individual_fund_flow(stock=code, market=market)
        if df is None or len(df) == 0:
            return ToolResult(content=f"未取到 {code} 资金流数据", is_error=True)

        recent = df.tail(5)
        lines = [f"【{code}】最近 {len(recent)} 日资金流向:"]
        lines.append(f"{'日期':<12} {'收盘':>7} {'涨跌幅':>8} {'主力净流入(万)':>14} {'超大单(万)':>10} {'大单(万)':>8}")
        lines.append("-" * 65)
        for _, r in recent.iterrows():
            lines.append(
                f"{str(r['日期']):<12} "
                f"{r.get('收盘价', 0):>7.2f} "
                f"{r.get('涨跌幅', 0):>+8.2f}% "
                f"{r.get('主力净流入-净额', 0)/10000:>14.1f} "
                f"{r.get('超大单净流入-净额', 0)/10000:>10.1f} "
                f"{r.get('大单净流入-净额', 0)/10000:>8.1f}"
            )
        return ToolResult(content="\n".join(lines))

    except Exception as e:
        log.error("capital_flow_failed", code=code, error=str(e))
        return ToolResult(content=f"获取 {code} 资金流失败：{e}", is_error=True)
