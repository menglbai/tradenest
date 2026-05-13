"""
================================================================================
文件：tradenest/tools/market.py
作用：实时行情 / K 线 / 基本信息 / 资金流向工具
================================================================================

【数据源策略】
实时行情支持多个数据源，按优先级依次尝试，失败自动降级：
  1. tencent   腾讯行情（qt.gtimg.cn）    —— 默认，稳定，容器/本机均可用
  2. sina      新浪行情（hq.sinajs.cn）   —— 备用，同样稳定
  3. ths       同花顺行情（d.10jqka.com.cn）—— 分时接口，含均价
  4. eastmoney 东方财富（AkShare）        —— 兜底，部分网络环境被反爬

通过环境变量 TRADENEST_QUOTE_SOURCES 配置优先级，逗号分隔：
  TRADENEST_QUOTE_SOURCES=tencent,sina,ths     # 默认
  TRADENEST_QUOTE_SOURCES=ths,tencent          # 同花顺优先
  TRADENEST_QUOTE_SOURCES=tencent              # 只用腾讯

历史K线 / 基本面 / 资金流向：AkShare 东财接口（本机网络一般可用）

================================================================================
"""

from __future__ import annotations

import os
from typing import Any

from tradenest.core.logging import get_logger
from tradenest.tools.base import ToolResult, register_tool

log = get_logger("tradenest.tools.market")


# ============================================================
# 数据源配置
# ============================================================

def _get_quote_sources() -> list[str]:
    """从环境变量读取数据源优先级列表。"""
    raw = os.environ.get("TRADENEST_QUOTE_SOURCES", "tencent,sina,ths")
    return [s.strip() for s in raw.split(",") if s.strip()]


# ============================================================
# 数据源 1：腾讯行情（qt.gtimg.cn）
# ============================================================
# 格式: v_sh600519="1~贵州茅台~600519~价格~昨收~今开~成交量(手)~...~涨跌额(31)~涨跌幅%(32)~最高(33)~最低(34)~...~成交额万(37)~换手率%(38)~..."

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


def _code_prefix(code: str, style: str = "tencent") -> str:
    """把裸代码加上市场前缀。style: tencent（sh/sz）或 sina（sh/sz 相同）。"""
    if code.startswith(("sh", "sz", "hk")):
        return code
    if code.startswith("6"):
        return "sh" + code
    if code.startswith(("0", "3")):
        return "sz" + code
    return "sh" + code


def _fetch_tencent(codes: list[str]) -> dict[str, dict]:
    """腾讯行情：返回 {裸code: {name,price,change,change_pct,open,high,low,prev_close,volume,amount,turnover,source}}。"""
    import requests
    prefixed = [_code_prefix(c) for c in codes]
    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
    r = requests.get(url, timeout=8, headers={"Referer": "https://gu.qq.com/"})
    result: dict[str, dict] = {}
    for line in r.text.strip().split("\n"):
        if "~" not in line:
            continue
        try:
            inner = line.split('"')[1]
            parts = inner.split("~")
            row: dict[str, Any] = {k: (parts[i].strip() if i < len(parts) else "") for k, i in _TENCENT_IDX.items()}
            row["source"] = "腾讯行情"
            if row.get("code"):
                result[row["code"]] = row
        except Exception:
            continue
    return result


# ============================================================
# 数据源 2：新浪行情（hq.sinajs.cn）
# ============================================================
# 格式: var hq_str_sh600519="名称,今开,昨收,现价,最高,最低,买一,卖一,成交量(股),成交额(元),...,日期,时间"

def _fetch_sina(codes: list[str]) -> dict[str, dict]:
    """新浪行情：返回 {裸code: {...}}。"""
    import requests
    prefixed = [_code_prefix(c) for c in codes]
    url = "https://hq.sinajs.cn/list=" + ",".join(prefixed)
    r = requests.get(url, timeout=8, headers={
        "Referer": "https://finance.sina.com.cn/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    })
    result: dict[str, dict] = {}
    for line in r.text.strip().split("\n"):
        if '"' not in line or "," not in line:
            continue
        try:
            # 提取前缀里的代码
            key_part = line.split("=")[0].split("_")[-1]  # sh600519
            bare_code = key_part[2:]  # 600519
            inner = line.split('"')[1]
            parts = inner.split(",")
            if len(parts) < 10:
                continue
            # 字段: 0=名称 1=今开 2=昨收 3=现价 4=最高 5=最低 6=买一 7=卖一 8=成交量(股) 9=成交额(元)
            volume_shares = int(parts[8]) if parts[8].strip() else 0
            volume_hand = volume_shares // 100
            amount_yuan = float(parts[9]) if parts[9].strip() else 0
            amount_wan = amount_yuan / 10000
            price = parts[3].strip()
            prev_close = parts[2].strip()
            change = f"{float(price) - float(prev_close):.2f}" if price and prev_close else ""
            change_pct = f"{(float(price) - float(prev_close)) / float(prev_close) * 100:.2f}" if price and prev_close and float(prev_close) else ""
            result[bare_code] = {
                "name":       parts[0].strip(),
                "code":       bare_code,
                "price":      price,
                "prev_close": prev_close,
                "open":       parts[1].strip(),
                "high":       parts[4].strip(),
                "low":        parts[5].strip(),
                "volume":     str(volume_hand),
                "amount":     f"{amount_wan:.0f}",
                "change":     change,
                "change_pct": change_pct,
                "turnover":   "",   # 新浪不直接给换手率
                "source":     "新浪行情",
            }
        except Exception:
            continue
    return result


# ============================================================
# 数据源 3：同花顺行情（d.10jqka.com.cn 分时接口）
# ============================================================
# 分时接口返回当日全量分时数据，最后一条即为最新/收盘价
# 字段：时间(HHMM),价格,成交额(元),均价,成交量(股)

def _fetch_ths(codes: list[str]) -> dict[str, dict]:
    """同花顺行情（分时接口）：返回 {裸code: {...}}。"""
    import requests, json, re
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://stockpage.10jqka.com.cn/",
    }
    result: dict[str, dict] = {}
    for code in codes:
        prefix = "sh" if code.startswith("6") else "sz"
        try:
            url = f"https://d.10jqka.com.cn/v4/time/hs_{code}/last.js"
            r = requests.get(url, headers=headers, timeout=8)
            raw = re.sub(r"^[^(]+\(", "", r.text).rstrip(");")
            data = json.loads(raw)
            stock = data.get(f"hs_{code}", {})
            time_data = stock.get("data", "")
            if not time_data:
                continue
            # 取最后一条分时记录
            entries = [e for e in time_data.strip().split(";") if e]
            last = entries[-1].split(",")
            if len(last) < 3:
                continue
            price = last[1]
            prev_close = str(stock.get("pre", ""))
            amount_yuan = float(last[2]) if last[2] else 0
            volume_shares = int(last[4]) if len(last) > 4 and last[4] else 0
            # 累计成交额（遍历所有分时条目）
            total_amount = sum(float(e.split(",")[2]) for e in entries if len(e.split(",")) > 2 and e.split(",")[2])
            total_volume = sum(int(e.split(",")[4]) for e in entries if len(e.split(",")) > 4 and e.split(",")[4])
            change = f"{float(price) - float(prev_close):.2f}" if price and prev_close else ""
            change_pct = f"{(float(price) - float(prev_close)) / float(prev_close) * 100:.2f}" if price and prev_close and float(prev_close) else ""
            result[code] = {
                "name":       stock.get("name", code),
                "code":       code,
                "price":      price,
                "prev_close": prev_close,
                "open":       "",   # 分时接口不直接给今开
                "high":       "",   # 不直接给最高
                "low":        "",   # 不直接给最低
                "volume":     str(total_volume // 100),  # 转为手
                "amount":     f"{total_amount / 10000:.0f}",  # 万元
                "change":     change,
                "change_pct": change_pct,
                "turnover":   "",
                "source":     "同花顺行情",
            }
        except Exception as e:
            log.debug("ths_quote_failed", code=code, error=str(e)[:80])
            continue
    return result


# ============================================================
# 数据源 4：东方财富（AkShare，兜底）
# ============================================================

def _fetch_eastmoney(codes: list[str]) -> dict[str, dict]:
    """东方财富行情（AkShare，可能被反爬）：返回 {裸code: {...}}。"""
    try:
        import akshare as ak
    except ImportError:
        return {}
    try:
        df = ak.stock_zh_a_spot_em()
        result: dict[str, dict] = {}
        for code in codes:
            target = df[df["代码"] == code]
            if target.empty:
                continue
            row = target.iloc[0]
            result[code] = {
                "name":       str(row.get("名称", "")),
                "code":       code,
                "price":      str(row.get("最新价", "")),
                "prev_close": str(row.get("昨收", "")),
                "open":       str(row.get("今开", "")),
                "high":       str(row.get("最高", "")),
                "low":        str(row.get("最低", "")),
                "volume":     str(row.get("成交量", "")),
                "amount":     str(int(row.get("成交额", 0) / 10000)),
                "change":     str(row.get("涨跌额", "")),
                "change_pct": str(row.get("涨跌幅", "")),
                "turnover":   str(row.get("换手率", "")),
                "source":     "东方财富",
            }
        return result
    except Exception:
        return {}


# ============================================================
# 统一入口：按配置依次尝试，自动降级
# ============================================================

_SOURCE_FUNCS = {
    "tencent":   _fetch_tencent,
    "sina":      _fetch_sina,
    "ths":       _fetch_ths,
    "eastmoney": _fetch_eastmoney,
}


def _fetch_quote_with_fallback(codes: list[str]) -> tuple[dict[str, dict], list[str]]:
    """
    按 TRADENEST_QUOTE_SOURCES 顺序依次尝试，返回 (data, tried_sources)。
    如果某个数据源成功拿到全部 codes 的数据就返回，否则降级到下一个。
    """
    sources = _get_quote_sources()
    tried: list[str] = []
    last_data: dict[str, dict] = {}

    for source_name in sources:
        fn = _SOURCE_FUNCS.get(source_name)
        if not fn:
            log.warning("unknown_quote_source", source=source_name)
            continue
        tried.append(source_name)
        try:
            data = fn(codes)
            if data:
                log.debug("quote_source_ok", source=source_name, codes=codes)
                return data, tried
            else:
                log.warning("quote_source_empty", source=source_name, codes=codes)
        except Exception as e:
            log.warning("quote_source_failed", source=source_name, error=str(e)[:100])

    return last_data, tried


# ============================================================
# 工具 1：实时行情（多源 + 自动降级）
# ============================================================

@register_tool(
    name="get_realtime_quote",
    description=(
        "获取 A 股的当前实时行情。"
        "返回：最新价、涨跌幅、涨跌额、开盘、最高、最低、昨收、成交量、成交额、换手率。"
        "数据来源按优先级自动切换（腾讯 → 新浪 → 东方财富）。"
        "用户问'XX 现在多少钱 / 今天涨了多少 / 实时价格'时调用。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "A 股 6 位代码，如 600519（贵州茅台）、000858（五粮液）",
            },
            "compare_sources": {
                "type": "boolean",
                "default": False,
                "description": "是否同时拉取多个数据源对比（true = 展示各平台价格差异）",
            },
        },
        "required": ["code"],
    },
)
async def get_realtime_quote(code: str, compare_sources: bool = False) -> ToolResult:
    """获取实时行情，支持多源对比。"""
    code = code.strip()

    if compare_sources:
        # 对比模式：同时拉所有可用数据源
        lines = [f"【{code}】多平台行情对比:"]
        lines.append(f"{'平台':<10} {'最新价':>8} {'涨跌幅':>8} {'今开':>8} {'最高':>8} {'最低':>8} {'成交额(万)':>12}")
        lines.append("-" * 65)
        any_ok = False
        for source_name, fn in _SOURCE_FUNCS.items():
            try:
                data = fn([code])
                row = data.get(code)
                if not row:
                    lines.append(f"{source_name:<10} {'（无数据）':>8}")
                    continue
                any_ok = True
                pct = row['change_pct']
                sign = "+" if pct and not str(pct).startswith("-") else ""
                lines.append(
                    f"{row['source']:<10} "
                    f"¥{row['price']:>7} "
                    f"{sign}{pct:>7}% "
                    f"¥{row['open']:>7} "
                    f"¥{row['high']:>7} "
                    f"¥{row['low']:>7} "
                    f"{row['amount']:>12} 万"
                )
            except Exception as e:
                lines.append(f"{source_name:<10} 失败: {str(e)[:40]}")
        if not any_ok:
            return ToolResult(content=f"所有数据源均无法获取 {code} 行情", is_error=True)
        lines.append("")
        lines.append("💡 价格差异通常 < 0.01 元（均来自交易所，仅延迟略有差异）")
        return ToolResult(content="\n".join(lines))

    # 普通模式：按优先级降级
    data, tried = _fetch_quote_with_fallback([code])
    row = data.get(code)
    if not row:
        return ToolResult(
            content=f"获取 {code} 行情失败（已尝试: {', '.join(tried)}），请检查代码是否正确。",
            is_error=True,
        )

    sign = "+" if not str(row["change"]).startswith("-") else ""
    turnover_line = f"  换手率:  {row['turnover']}%\n" if row.get("turnover") else ""
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
        f"{turnover_line}"
        f"  数据来源: {row['source']}"
    )
    return ToolResult(content=content, metadata={"row": row})


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
                "description": "复权: qfq=前复权 / hfq=后复权 / 空=不复权",
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

    from datetime import datetime, timedelta
    end_date = datetime.now().strftime("%Y%m%d")
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
        rows.append(f"{'日期':<12} {'开盘':>8} {'收盘':>8} {'最高':>8} {'最低':>8} {'成交量':>10} {'涨跌幅':>8}")
        rows.append("-" * 68)
        for _, r in df_recent.iterrows():
            rows.append(
                f"{r['日期']!s:<12} "
                f"{r['开盘']:>8.2f} "
                f"{r['收盘']:>8.2f} "
                f"{r['最高']:>8.2f} "
                f"{r['最低']:>8.2f} "
                f"{int(r['成交量']):>10} "
                f"{r.get('涨跌幅', 0):>+8.2f}%"
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
    """获取资金流向。"""
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
                f"{r.get('主力净流入-净额', 0) / 10000:>14.1f} "
                f"{r.get('超大单净流入-净额', 0) / 10000:>10.1f} "
                f"{r.get('大单净流入-净额', 0) / 10000:>8.1f}"
            )
        return ToolResult(content="\n".join(lines))

    except Exception as e:
        log.error("capital_flow_failed", code=code, error=str(e))
        return ToolResult(content=f"获取 {code} 资金流失败：{e}", is_error=True)
