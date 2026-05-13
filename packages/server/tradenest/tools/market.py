"""
================================================================================
文件：tradenest/tools/market.py
作用：实时行情 / K 线 / 基本信息工具（基于 AkShare）
================================================================================

【核心业务功能】
为 Agent 提供"拉取股票实时数据"的能力。

封装 AkShare 这个开源 A/H/US 股数据库，对外暴露统一的工具接口。

包含的工具：
1. **get_realtime_quote**: 实时行情（最新价 / 涨跌幅 / 成交量 ...）
2. **get_history_kline**: 历史 K 线（日 / 周 / 月）
3. **get_basic_info**: 公司基本信息（名称 / 行业 / 市值 / PE/PB ...）
4. **get_capital_flow**: 资金流向（北向 / 主力 / 散户）

【网络容错策略】
AkShare 实际从东方财富等公开页面抓数据，可能因为：
- 网络抖动
- 代理拦截
- 接口下线

而失败。每个工具都用 try-except 包装，失败时返回 is_error=True 的 ToolResult，
不让 Agent loop 整个崩。

【合规边界】
本模块只**返回事实数据**，不做"建议"。
LLM 拿到这些数据后，由 system prompt 严格约束输出风格（不出建议）。

================================================================================
"""

from __future__ import annotations

import os
from typing import Any

from tradenest.core.logging import get_logger
from tradenest.tools.base import ToolResult, register_tool

log = get_logger("tradenest.tools.market")


# ============================================================
# 内部辅助：避开 mitm 代理
# ============================================================
# AkShare 抓的是公开网页（东财 / 新浪 / 雪球），这些走容器 mitm 代理
# 通常会被 reset。这里设置 NO_PROXY 让请求直连。
# 注意：在生产部署时，用户机器一般能直接访问东财，没问题。
_AKSHARE_HOSTS = (
    "eastmoney.com,push2.eastmoney.com,push2his.eastmoney.com,"
    "datacenter.eastmoney.com,fund.eastmoney.com,"
    "sinajs.cn,hq.sinajs.cn,"
    "xueqiu.com,stock.xueqiu.com"
)


def _setup_no_proxy() -> None:
    """让 AkShare 调用时绕过代理。"""
    existing = os.environ.get("NO_PROXY", "")
    if "eastmoney.com" not in existing:
        os.environ["NO_PROXY"] = (existing + "," + _AKSHARE_HOSTS).strip(",")
    existing_lower = os.environ.get("no_proxy", "")
    if "eastmoney.com" not in existing_lower:
        os.environ["no_proxy"] = (existing_lower + "," + _AKSHARE_HOSTS).strip(",")


_setup_no_proxy()


# ============================================================
# 工具：实时行情
# ============================================================

@register_tool(
    name="get_realtime_quote",
    description=(
        "获取 A 股 / 港股 / 美股的当前实时行情。"
        "返回内容包括：最新价、涨跌幅、涨跌额、成交量、成交额、换手率、振幅、最高/最低价。"
        "用户问'XX 现在多少钱 / 今天涨了多少 / 实时价格'时调用。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "股票代码。A 股 6 位数字（如 600519 贵州茅台），港股 5 位数字（00700），美股字母（AAPL）。",
            },
            "market": {
                "type": "string",
                "enum": ["A", "HK", "US", "auto"],
                "default": "auto",
                "description": "市场类型，auto 表示按代码自动判断",
            },
        },
        "required": ["code"],
    },
)
async def get_realtime_quote(code: str, market: str = "auto") -> ToolResult:
    """获取实时行情。"""
    try:
        import akshare as ak  # 延迟 import，避免启动时报错
    except ImportError:
        return ToolResult(
            content="AkShare 未安装。请 `uv pip install akshare`。",
            is_error=True,
        )
    
    code = code.strip()
    
    # 自动判断市场
    if market == "auto":
        if code.isdigit() and len(code) == 6:
            market = "A"
        elif code.isdigit() and len(code) == 5:
            market = "HK"
        else:
            market = "US"
    
    try:
        if market == "A":
            # 拉全 A 股快照，再过滤
            df = ak.stock_zh_a_spot_em()
            target = df[df['代码'] == code]
            if len(target) == 0:
                return ToolResult(content=f"未找到 A 股代码 {code}", is_error=True)
            row = target.iloc[0]
            
            # 关键字段提取
            content = (
                f"【{row['名称']} ({code})】实时行情:\n"
                f"  最新价: ¥{row['最新价']}\n"
                f"  涨跌幅: {row['涨跌幅']}%\n"
                f"  涨跌额: {row['涨跌额']}\n"
                f"  开盘价: ¥{row.get('今开', 'N/A')}\n"
                f"  最高价: ¥{row.get('最高', 'N/A')}\n"
                f"  最低价: ¥{row.get('最低', 'N/A')}\n"
                f"  成交量: {row.get('成交量', 'N/A')} 手\n"
                f"  成交额: ¥{row.get('成交额', 'N/A'):.0f}\n"
                f"  换手率: {row.get('换手率', 'N/A')}%\n"
                f"  振幅: {row.get('振幅', 'N/A')}%\n"
                f"  市盈率(动态): {row.get('市盈率-动态', 'N/A')}\n"
                f"  市净率: {row.get('市净率', 'N/A')}\n"
                f"  总市值: ¥{row.get('总市值', 0):.0f}\n"
                f"  数据来源: 东方财富"
            )
            return ToolResult(content=content, metadata={"row": row.to_dict()})
        
        elif market == "HK":
            df = ak.stock_hk_spot_em()
            target = df[df['代码'] == code]
            if len(target) == 0:
                return ToolResult(content=f"未找到港股代码 {code}", is_error=True)
            row = target.iloc[0]
            content = (
                f"【{row['名称']} ({code})】港股实时:\n"
                f"  最新价: HK${row['最新价']}\n"
                f"  涨跌幅: {row['涨跌幅']}%\n"
                f"  数据来源: 东方财富"
            )
            return ToolResult(content=content, metadata={"row": row.to_dict()})
        
        elif market == "US":
            df = ak.stock_us_spot_em()
            target = df[df['代码'].str.endswith(code, na=False)]
            if len(target) == 0:
                return ToolResult(content=f"未找到美股代码 {code}", is_error=True)
            row = target.iloc[0]
            content = (
                f"【{row['名称']} ({code})】美股实时:\n"
                f"  最新价: ${row['最新价']}\n"
                f"  涨跌幅: {row['涨跌幅']}%\n"
                f"  数据来源: 东方财富"
            )
            return ToolResult(content=content, metadata={"row": row.to_dict()})
        
        else:
            return ToolResult(content=f"未知市场类型: {market}", is_error=True)
    
    except Exception as e:
        log.error("realtime_quote_failed", code=code, market=market, error=str(e))
        return ToolResult(
            content=(
                f"获取 {code} 实时行情失败：{type(e).__name__}: {e}\n"
                f"可能原因：网络问题 / 代理拦截 / 数据源接口变更。"
                f"建议：检查网络后重试，或换数据源。"
            ),
            is_error=True,
        )


# ============================================================
# 工具：历史 K 线
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
                "description": "复权类型: qfq=前复权（推荐）/ hfq=后复权 / 不填=不复权",
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
    """获取历史 K 线（A 股）。"""
    try:
        import akshare as ak
        from datetime import datetime, timedelta
    except ImportError:
        return ToolResult(content="AkShare 未安装", is_error=True)
    
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")  # *2 防节假日
    
    try:
        df = ak.stock_zh_a_hist(
            symbol=code, period=period,
            start_date=start_date, end_date=end_date,
            adjust=adjust,
        )
        if df is None or len(df) == 0:
            return ToolResult(content=f"未取到 {code} 的历史 K 线", is_error=True)
        
        # 取最近 N 条
        df_recent = df.tail(days)
        
        # 简化输出（避免太长）
        rows: list[str] = [f"【{code}】最近 {len(df_recent)} 个 {period} K 线（复权: {adjust}）:"]
        rows.append(f"日期       | 开盘    | 收盘    | 最高    | 最低    | 成交量    | 涨跌幅")
        rows.append("-" * 80)
        for _, r in df_recent.iterrows():
            rows.append(
                f"{r['日期']} | "
                f"{r['开盘']:7.2f} | "
                f"{r['收盘']:7.2f} | "
                f"{r['最高']:7.2f} | "
                f"{r['最低']:7.2f} | "
                f"{int(r['成交量']):>9} | "
                f"{r.get('涨跌幅', 0):+.2f}%"
            )
        
        return ToolResult(
            content="\n".join(rows),
            metadata={"count": len(df_recent), "code": code},
        )
    except Exception as e:
        log.error("history_kline_failed", code=code, error=str(e))
        return ToolResult(
            content=f"获取 {code} K 线失败：{e}",
            is_error=True,
        )


# ============================================================
# 工具：基本信息
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
        # 用 stock_individual_info_em 拿基本信息
        df = ak.stock_individual_info_em(symbol=code)
        if df is None or len(df) == 0:
            return ToolResult(content=f"未找到 {code} 的基本信息", is_error=True)
        
        # 转成 dict 输出
        info = dict(zip(df['item'], df['value']))
        lines = [f"【{code}】公司基本信息:"]
        for k, v in info.items():
            lines.append(f"  {k}: {v}")
        
        return ToolResult(content="\n".join(lines), metadata=info)
    except Exception as e:
        log.error("basic_info_failed", code=code, error=str(e))
        return ToolResult(content=f"获取基本信息失败：{e}", is_error=True)


# ============================================================
# 工具：资金流向
# ============================================================

@register_tool(
    name="get_capital_flow",
    description=(
        "获取 A 股个股的资金流向：主力流入 / 散户流入 / 北向资金等。"
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
    """获取资金流向。"""
    try:
        import akshare as ak
    except ImportError:
        return ToolResult(content="AkShare 未安装", is_error=True)
    
    try:
        # 个股资金流（最近 N 日）
        # 用 stock_individual_fund_flow，参数需要市场代码 sh / sz
        market = "sh" if code.startswith("6") else "sz"
        df = ak.stock_individual_fund_flow(stock=code, market=market)
        if df is None or len(df) == 0:
            return ToolResult(content=f"未取到 {code} 资金流", is_error=True)
        
        # 取最近 5 天
        recent = df.tail(5)
        lines = [f"【{code}】最近 {len(recent)} 日资金流向:"]
        lines.append("日期       | 收盘   | 涨跌幅  | 主力净流入(万) | 超大单(万) | 大单(万) | 中单(万) | 小单(万)")
        lines.append("-" * 110)
        for _, r in recent.iterrows():
            lines.append(
                f"{r['日期']} | "
                f"{r.get('收盘价', 0):6.2f} | "
                f"{r.get('涨跌幅', 0):+6.2f}% | "
                f"{r.get('主力净流入-净额', 0)/10000:>14.1f} | "
                f"{r.get('超大单净流入-净额', 0)/10000:>10.1f} | "
                f"{r.get('大单净流入-净额', 0)/10000:>8.1f} | "
                f"{r.get('中单净流入-净额', 0)/10000:>8.1f} | "
                f"{r.get('小单净流入-净额', 0)/10000:>8.1f}"
            )
        
        return ToolResult(content="\n".join(lines))
    except Exception as e:
        log.error("capital_flow_failed", code=code, error=str(e))
        return ToolResult(content=f"获取资金流失败：{e}", is_error=True)
