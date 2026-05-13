"""
================================================================================
文件：tradenest/tools/macro.py
作用：宏观数据工具 —— 国际行情 + 政策新闻 + 今日盘中资金流
================================================================================

【工具列表】
1. get_global_markets   国际市场行情（美股三大指数 + 恒生 + 日经 + 黄金 + 原油 + 汇率）
2. get_macro_news       宏观政策新闻（财联社电报 + 新浪财经，多源聚合）
3. get_intraday_flow    今日盘中实时资金流（东财分钟级接口，盘中可调）

【数据源】
- 国际行情：新浪行情（hq.sinajs.cn）—— 稳定，容器可用
- 宏观新闻：AkShare 财联社全球要闻 + 新浪财经宏观 —— 双源聚合
- 盘中资金流：东财 push2 接口 —— 容器可用，分钟级

================================================================================
"""

from __future__ import annotations

import re
import json
from datetime import datetime
from typing import Any

from tradenest.core.logging import get_logger
from tradenest.tools.base import ToolResult, register_tool

log = get_logger("tradenest.tools.macro")


# ============================================================
# 工具 1：国际市场行情
# ============================================================

# 新浪行情支持的国际品种
_GLOBAL_SYMBOLS = {
    # 美股指数
    "int_dji":     ("道琼斯",     "美股"),
    "int_nasdaq":  ("纳斯达克",   "美股"),
    "int_sp500":   ("标普500",    "美股"),
    # 亚太
    "int_hangseng":("恒生指数",   "港股"),
    "int_nikkei":  ("日经225",    "日股"),
    # 汇率
    "fx_usdcnh":   ("美元/人民币", "汇率"),
    "fx_eurousd":  ("欧元/美元",  "汇率"),
    # 大宗商品（新浪期货前缀）
    "hf_GC":       ("黄金",       "大宗"),
    "hf_CL":       ("原油(WTI)",  "大宗"),
}


def _fetch_global_sina(symbols: list[str]) -> list[dict]:
    """用新浪行情接口拉国际品种。"""
    import requests
    url = "https://hq.sinajs.cn/list=" + ",".join(symbols)
    r = requests.get(url, timeout=8, headers={
        "Referer": "https://finance.sina.com.cn/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    })
    result = []
    for line in r.text.strip().split("\n"):
        if '"' not in line or "," not in line:
            continue
        try:
            key = line.split("=")[0].strip().split(" ")[-1]  # var hq_str_int_dji → int_dji
            # 去掉 hq_str_ 前缀
            key = re.sub(r"^hq_str_", "", key)
            inner = line.split('"')[1]
            parts = inner.split(",")
            if not parts[0] or not parts[1]:
                continue
            name_cn, category = _GLOBAL_SYMBOLS.get(key, (key, "其他"))
            # 不同品种字段数不同
            # 指数格式：名称,价格,涨跌额,涨跌幅
            # 汇率格式：名称,价格,涨跌额,涨跌幅 (有时更多字段)
            change = parts[2].strip() if len(parts) > 2 else ""
            pct = parts[3].strip().rstrip(';"') if len(parts) > 3 else ""
            result.append({
                "key":      key,
                "name":     name_cn,
                "category": category,
                "price":    parts[1].strip(),
                "change":   change,
                "pct":      pct,
                "source":   "新浪行情",
            })
        except Exception:
            continue
    return result


@register_tool(
    name="get_global_markets",
    description=(
        "获取国际市场实时行情。"
        "包含：美股三大指数（道指/纳指/标普）、亚太（恒生/日经）、"
        "汇率（美元/人民币、欧元/美元）、大宗商品（黄金/原油）。"
        "用户问'外盘怎么样 / 美股涨跌 / 黄金价格 / 汇率 / 国际形势'时调用。"
        "分析 A 股前也建议先看外盘。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "categories": {
                "type": "array",
                "items": {"type": "string", "enum": ["美股", "港股", "日股", "汇率", "大宗", "全部"]},
                "default": ["全部"],
                "description": "要查的品种分类，默认全部",
            },
        },
        "required": [],
    },
)
async def get_global_markets(categories: list[str] | None = None) -> ToolResult:
    """获取国际市场行情（新浪行情，多品种）。"""
    if not categories or "全部" in categories:
        want_all = True
    else:
        want_all = False

    symbols = list(_GLOBAL_SYMBOLS.keys())
    try:
        rows = _fetch_global_sina(symbols)
    except Exception as e:
        log.error("global_markets_failed", error=str(e))
        return ToolResult(content=f"获取国际行情失败：{e}", is_error=True)

    if not rows:
        return ToolResult(content="国际行情数据为空，可能是非交易时段", is_error=True)

    # 按分类分组输出
    groups: dict[str, list[dict]] = {}
    for row in rows:
        cat = row["category"]
        if not want_all and cat not in categories:
            continue
        groups.setdefault(cat, []).append(row)

    lines = [f"【国际市场行情】更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
    for cat, items in groups.items():
        lines.append(f"\n▌ {cat}")
        for item in items:
            price = item["price"]
            pct = item["pct"]
            change = item["change"]
            sign = "+" if pct and not str(pct).startswith("-") else ""
            lines.append(f"  {item['name']:<12} {price:>10}   {sign}{change} ({sign}{pct}%)")

    lines.append(f"\n  数据来源: {rows[0]['source']}")
    return ToolResult(content="\n".join(lines), metadata={"rows": rows})


# ============================================================
# 工具 2：宏观政策新闻（多源聚合）
# ============================================================

def _fetch_cls_akshare() -> list[dict]:
    """财联社全球要闻（AkShare 封装）。"""
    try:
        import akshare as ak
        df = ak.stock_info_global_cls()
        result = []
        for _, row in df.iterrows():
            result.append({
                "time":    f"{row.get('发布日期', '')} {row.get('发布时间', '')}",
                "title":   str(row.get("标题", "")),
                "content": str(row.get("内容", "")),
                "source":  "财联社",
            })
        return result
    except Exception as e:
        log.warning("cls_akshare_failed", error=str(e)[:80])
        return []


def _fetch_sina_macro() -> list[dict]:
    """新浪财经宏观新闻。"""
    import requests
    try:
        r = requests.get(
            "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2515&num=15&page=1&r=0.5",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn/",
            },
            timeout=8,
        )
        data = r.json()
        result = []
        for item in data.get("result", {}).get("data", []):
            result.append({
                "time":    item.get("ctime", ""),
                "title":   item.get("title", ""),
                "content": item.get("intro", ""),
                "source":  "新浪财经",
            })
        return result
    except Exception as e:
        log.warning("sina_macro_failed", error=str(e)[:80])
        return []


def _fetch_eastmoney_news() -> list[dict]:
    """东财快讯。"""
    import requests
    try:
        r = requests.get(
            "https://newsapi.eastmoney.com/kuaixun/v1/getlist_115_ajaxResult_10_1_.html",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.eastmoney.com/"},
            timeout=8,
        )
        # 去掉 var ajaxResult= 前缀
        raw = re.sub(r"^var ajaxResult=", "", r.text.strip())
        data = json.loads(raw)
        result = []
        for item in data.get("LivesList", []):
            result.append({
                "time":    item.get("showtime", ""),
                "title":   item.get("title", ""),
                "content": item.get("digest", ""),
                "source":  "东方财富",
            })
        return result
    except Exception as e:
        log.warning("eastmoney_news_failed", error=str(e)[:80])
        return []


@register_tool(
    name="get_macro_news",
    description=(
        "获取宏观政策和市场重要新闻。"
        "多平台聚合：财联社电报、新浪财经、东方财富快讯。"
        "用户问'今天有什么政策 / 宏观面怎么样 / 有什么大新闻 / 市场情绪'时调用。"
        "分析个股或大盘前建议先看宏观新闻。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "default": 10,
                "minimum": 3,
                "maximum": 30,
                "description": "每个来源最多返回多少条",
            },
            "sources": {
                "type": "array",
                "items": {"type": "string", "enum": ["财联社", "新浪财经", "东方财富", "全部"]},
                "default": ["全部"],
                "description": "数据来源，默认全部",
            },
        },
        "required": [],
    },
)
async def get_macro_news(limit: int = 10, sources: list[str] | None = None) -> ToolResult:
    """多源聚合宏观新闻：财联社 + 新浪财经 + 东财快讯。"""
    want_all = not sources or "全部" in sources

    fetchers = {
        "财联社":  _fetch_cls_akshare,
        "新浪财经": _fetch_sina_macro,
        "东方财富": _fetch_eastmoney_news,
    }

    all_items: list[dict] = []
    source_stats: list[str] = []

    for name, fn in fetchers.items():
        if not want_all and name not in sources:
            continue
        try:
            items = fn()[:limit]
            all_items.extend(items)
            source_stats.append(f"{name}({len(items)}条)")
            log.debug("macro_news_fetched", source=name, count=len(items))
        except Exception as e:
            source_stats.append(f"{name}(失败)")
            log.warning("macro_news_source_failed", source=name, error=str(e)[:80])

    if not all_items:
        return ToolResult(content="所有新闻来源均无法获取，请检查网络", is_error=True)

    lines = [
        f"【宏观市场新闻】{datetime.now().strftime('%Y-%m-%d %H:%M')} "
        f"| 来源: {', '.join(source_stats)}"
    ]

    # 按来源分组展示
    by_source: dict[str, list[dict]] = {}
    for item in all_items:
        by_source.setdefault(item["source"], []).append(item)

    for src, items in by_source.items():
        lines.append(f"\n▌ {src}")
        for item in items[:limit]:
            t = item.get("time", "")
            title = item.get("title", "") or item.get("content", "")[:40]
            content = item.get("content", "")
            # 内容太长就截断
            if len(content) > 80:
                content = content[:80] + "..."
            if title:
                lines.append(f"  [{t}] {title}")
            if content and content != title:
                lines.append(f"    └ {content}")

    return ToolResult(content="\n".join(lines), metadata={"total": len(all_items)})


# ============================================================
# 工具 3：今日盘中实时资金流
# ============================================================

def _secid(code: str) -> str:
    """转换为东财 secid 格式：1.600519（沪）/ 0.000858（深）。"""
    return f"1.{code}" if code.startswith("6") else f"0.{code}"


def _parse_fflow(text: str) -> list[str]:
    """解析东财资金流 JSONP。"""
    # 可能返回多个 jQuery(...)jQuery(...)
    m = re.match(r"jQuery\((.+)\)$", text.strip(), re.DOTALL)
    if m:
        raw = m.group(1)
    else:
        idx = text.rfind(")")
        raw = text[7:idx]
    data = json.loads(raw)
    return data.get("data", {}).get("klines", [])


@register_tool(
    name="get_intraday_flow",
    description=(
        "获取个股今日盘中实时资金流向（分钟级）。"
        "返回：今日累计主力净流入/超大单/大单/中单/小单，以及最近1小时的分钟走势。"
        "盘中任意时刻可调用，数据实时更新。"
        "用户问'今天主力在买还是卖 / 今日资金流向 / 现在资金面'时调用。"
        "注意：收盘后调用返回当日最终数据。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "A 股 6 位代码，如 600519",
            },
            "show_minutes": {
                "type": "integer",
                "default": 30,
                "minimum": 5,
                "maximum": 120,
                "description": "展示最近 N 分钟的分钟走势，默认30分钟",
            },
        },
        "required": ["code"],
    },
)
async def get_intraday_flow(code: str, show_minutes: int = 30) -> ToolResult:
    """获取今日盘中资金流（东财分钟级接口）。"""
    import requests
    code = code.strip()
    secid = _secid(code)

    try:
        r = requests.get(
            f"https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
            f"?lmt=0&klt=1&secid={secid}"
            f"&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56"
            f"&cb=jQuery",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://data.eastmoney.com/zjlx/" + code + ".html",
            },
            timeout=10,
        )
        klines = _parse_fflow(r.text)
    except Exception as e:
        log.error("intraday_flow_failed", code=code, error=str(e))
        return ToolResult(content=f"获取 {code} 盘中资金流失败：{e}", is_error=True)

    if not klines:
        return ToolResult(content=f"{code} 今日无盘中资金流数据（可能未开市）", is_error=True)

    # 字段：时间, 主力净(元), 小单净(元), 散户净(元), 大单净(元), 超大单净(元)
    # 注意：这些是累计值，不是增量
    def parse_kline(k: str) -> dict:
        parts = k.split(",")
        def to_wan(v):
            try:
                return float(v) / 10000
            except Exception:
                return 0.0
        return {
            "time":   parts[0] if parts else "",
            "zl":     to_wan(parts[1]),   # 主力净流入（万）
            "xd":     to_wan(parts[2]),   # 小单
            "sd":     to_wan(parts[3]),   # 散户
            "dd":     to_wan(parts[4]),   # 大单
            "chd":    to_wan(parts[5]),   # 超大单
        }

    parsed = [parse_kline(k) for k in klines]
    latest = parsed[-1]

    # 计算今日最终数据（用最后一条，都是累计值）
    zl_yi = latest["zl"] / 10000       # 转亿
    chd_yi = latest["chd"] / 10000
    dd_yi = latest["dd"] / 10000
    xd_yi = latest["xd"] / 10000
    sd_yi = latest["sd"] / 10000

    def fmt_yi(v: float) -> str:
        sign = "+" if v >= 0 else ""
        return f"{sign}{v:.2f}亿"

    def flow_bar(v: float, scale: float = 1.0) -> str:
        """简单文字进度条表示流入/流出方向。"""
        blocks = int(abs(v) / scale)
        blocks = min(blocks, 10)
        if v >= 0:
            return "█" * blocks + f" 净流入"
        else:
            return "▓" * blocks + f" 净流出"

    scale = max(abs(zl_yi), 0.1)

    lines = [
        f"【{code}】今日盘中资金流向  {latest['time']}",
        f"",
        f"  主力净流入:  {fmt_yi(zl_yi):>12}  {flow_bar(zl_yi, scale/10)}",
        f"  ├ 超大单:    {fmt_yi(chd_yi):>12}",
        f"  └ 大单:      {fmt_yi(dd_yi):>12}",
        f"  散户净流入:  {fmt_yi(sd_yi):>12}  {flow_bar(sd_yi, scale/10)}",
        f"  小单净流入:  {fmt_yi(xd_yi):>12}",
        f"",
    ]

    # 最近 N 分钟走势
    recent = parsed[-show_minutes:]
    lines.append(f"  最近 {len(recent)} 分钟主力净流入走势（累计，万元）:")
    lines.append(f"  {'时间':<18} {'主力净':>10} {'超大单':>10} {'大单':>10} {'趋势'}")
    lines.append("  " + "-" * 58)

    prev_zl = recent[0]["zl"] if len(recent) > 1 else 0
    for row in recent:
        # 本分钟增量（当前累计 - 上一分钟累计）
        incr = row["zl"] - prev_zl
        trend = "▲" if incr > 0 else ("▼" if incr < 0 else "─")
        lines.append(
            f"  {row['time']:<18} "
            f"{row['zl']:>+10.0f} "
            f"{row['chd']:>+10.0f} "
            f"{row['dd']:>+10.0f} "
            f"{trend}"
        )
        prev_zl = row["zl"]

    lines.append(f"\n  数据来源: 东方财富（{len(klines)}条分钟数据）")
    return ToolResult(content="\n".join(lines), metadata={"latest": latest, "total_minutes": len(klines)})
