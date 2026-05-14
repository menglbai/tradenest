"""
================================================================================
文件：tradenest/tools/sector.py
作用：选股 + 市场热点工具
================================================================================

工具列表：
  get_market_leaders    — 涨跌幅榜 / 成交额榜 / 换手率榜（多维度 top N）
  get_market_sentiment  — 市场情绪：涨跌家数、涨停跌停数、成交量分布

数据来源：
  - 新浪行情（vip.stock.finance.sina.com.cn）— 容器/本机均可用
  - 全部同步函数在线程池执行，不阻塞 asyncio

================================================================================
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

from tradenest.core.logging import get_logger
from tradenest.tools.base import ToolResult, register_tool

log = get_logger("tradenest.tools.sector")

# ── 新浪行情接口 ──
_SINA_LIST_URL = (
    "https://vip.stock.finance.sina.com.cn"
    "/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
)
_SINA_HEADERS = {"Referer": "https://finance.sina.com.cn/"}
_TIMEOUT = 8

# ── 榜单定义 ──
RANK_CONFIGS: dict[str, dict[str, Any]] = {
    "gainers":    {"sort": "changepercent", "asc": 0, "label": "涨幅榜"},
    "losers":     {"sort": "changepercent", "asc": 1, "label": "跌幅榜"},
    "turnover":   {"sort": "turnoverratio",  "asc": 0, "label": "换手率榜"},
    "amount":     {"sort": "amount",         "asc": 0, "label": "成交额榜"},
    "volume":     {"sort": "volume",         "asc": 0, "label": "成交量榜"},
}

# ── 市场节点 ──
NODE_MAP = {
    "all":  "hs_a",   # 沪深A股
    "sh":   "sh_a",   # 上证
    "sz":   "sz_a",   # 深证
    "cyb":  "cyb",    # 创业板
    "kcb":  "kcb",    # 科创板
}


# ══════════════════════════════════════════════════════════
#  内部工具函数
# ══════════════════════════════════════════════════════════

def _fetch_rank(sort: str, asc: int, node: str, num: int) -> list[dict]:
    """从新浪拉一页榜单数据，返回解析后的列表。"""
    params = {
        "page": 1, "num": num,
        "sort": sort, "asc": asc,
        "node": node, "symbol": "",
    }
    try:
        r = requests.get(_SINA_LIST_URL, params=params, headers=_SINA_HEADERS, timeout=_TIMEOUT)
        data = json.loads(r.text)
        return data or []
    except Exception as e:
        log.warning("sector_fetch_failed", sort=sort, node=node, error=str(e))
        return []


def _fmt_amount(val: str | float) -> str:
    """成交额：转成亿元。"""
    try:
        v = float(val)
        if v >= 1e8:
            return f"{v/1e8:.2f}亿"
        elif v >= 1e4:
            return f"{v/1e4:.0f}万"
        return str(v)
    except Exception:
        return str(val)


def _fmt_item(item: dict, rank_type: str) -> str:
    """格式化单条榜单数据为可读文本。"""
    name   = item.get("name", "")
    code   = item.get("symbol", "")
    price  = item.get("trade", "")
    pct    = item.get("changepercent", "")
    amount = _fmt_amount(item.get("amount", 0))
    turn   = item.get("turnoverratio", "")

    try:
        pct_f = float(pct)
        sign = "+" if pct_f > 0 else ""
        pct_str = f"{sign}{pct_f:.2f}%"
    except Exception:
        pct_str = f"{pct}%"

    if rank_type == "amount":
        return f"{name}({code})  ¥{price}  {pct_str}  成交额:{amount}  换手:{turn}%"
    elif rank_type == "turnover":
        return f"{name}({code})  ¥{price}  {pct_str}  换手:{turn}%  成交额:{amount}"
    else:
        return f"{name}({code})  ¥{price}  {pct_str}  成交额:{amount}"


# ══════════════════════════════════════════════════════════
#  工具 1：多维度选股榜单
# ══════════════════════════════════════════════════════════

@register_tool(
    name="get_market_leaders",
    description=(
        "获取 A 股市场多维度排行榜：涨幅榜/跌幅榜/成交额榜/换手率榜/成交量榜。"
        "用户问'今天涨幅最大的股票'、'最活跃的股票'、'主力在炒什么'、"
        "'今天哪些股票成交最大'等场景时调用。"
        "支持按市场筛选：全部A股/上证/深证/创业板/科创板。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "rank_types": {
                "type": "array",
                "items": {"type": "string", "enum": ["gainers", "losers", "turnover", "amount", "volume"]},
                "description": "榜单类型列表。gainers=涨幅榜, losers=跌幅榜, turnover=换手率, amount=成交额, volume=成交量。默认 ['gainers','losers','amount']",
                "default": ["gainers", "losers", "amount"],
            },
            "market": {
                "type": "string",
                "enum": ["all", "sh", "sz", "cyb", "kcb"],
                "description": "市场范围。all=沪深A股(默认), sh=上证, sz=深证, cyb=创业板, kcb=科创板",
                "default": "all",
            },
            "top_n": {
                "type": "integer",
                "description": "每个榜取前 N 名，默认 10，最多 30",
                "default": 10,
                "minimum": 3,
                "maximum": 30,
            },
        },
        "required": [],
    },
)
async def get_market_leaders(
    rank_types: list[str] | None = None,
    market: str = "all",
    top_n: int = 10,
) -> ToolResult:
    import asyncio

    if rank_types is None:
        rank_types = ["gainers", "losers", "amount"]

    # 过滤无效类型
    rank_types = [r for r in rank_types if r in RANK_CONFIGS]
    if not rank_types:
        rank_types = ["gainers", "losers", "amount"]

    top_n  = max(3, min(30, top_n))
    node   = NODE_MAP.get(market, "hs_a")
    market_name = {"all":"沪深A股","sh":"上证A股","sz":"深证A股","cyb":"创业板","kcb":"科创板"}.get(market,"A股")

    ts = time.strftime("%Y-%m-%d %H:%M")

    # 并发拉多个榜单
    loop = asyncio.get_event_loop()
    tasks = {
        rt: loop.run_in_executor(
            None,
            _fetch_rank,
            RANK_CONFIGS[rt]["sort"],
            RANK_CONFIGS[rt]["asc"],
            node,
            top_n,
        )
        for rt in rank_types
    }
    results = {rt: await fut for rt, fut in tasks.items()}

    lines = [f"【{market_name}市场热点榜单】{ts}", ""]

    for rt in rank_types:
        cfg   = RANK_CONFIGS[rt]
        items = results[rt]
        lines.append(f"▌ {cfg['label']} TOP{top_n}")
        if not items:
            lines.append("  （暂无数据）")
        else:
            for i, item in enumerate(items[:top_n], 1):
                lines.append(f"  {i:2}. {_fmt_item(item, rt)}")
        lines.append("")

    log.info("market_leaders_fetched", rank_types=rank_types, market=market, top_n=top_n)
    return ToolResult(content="\n".join(lines))


# ══════════════════════════════════════════════════════════
#  工具 2：市场情绪（涨跌家数 + 涨跌停）
# ══════════════════════════════════════════════════════════

@register_tool(
    name="get_market_sentiment",
    description=(
        "获取 A 股今日市场情绪统计：涨跌家数、涨停/跌停数量、平盘数、"
        "涨跌幅分布。"
        "用户问'今天市场怎么样'、'今天赚钱效应如何'、'几家涨停'、"
        "'整体情绪好不好'时调用。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "market": {
                "type": "string",
                "enum": ["all", "sh", "sz"],
                "description": "市场范围：all=沪深(默认), sh=上证, sz=深证",
                "default": "all",
            }
        },
        "required": [],
    },
)
async def get_market_sentiment(market: str = "all") -> ToolResult:
    import asyncio

    node = NODE_MAP.get(market, "hs_a")
    market_name = {"all":"沪深A股","sh":"上证","sz":"深证"}.get(market,"A股")
    ts = time.strftime("%Y-%m-%d %H:%M")

    # 拉全量（取最多 1500 只，新浪限制）分批拉
    loop = asyncio.get_event_loop()

    async def fetch_page(page: int) -> list[dict]:
        return await loop.run_in_executor(
            None,
            lambda: _fetch_rank("changepercent", 0, node, 100),  # 每页100
        )

    # 拉涨幅最高和最低各100只，估算分布
    gainers_data = await loop.run_in_executor(None, _fetch_rank, "changepercent", 0, node, 100)
    losers_data  = await loop.run_in_executor(None, _fetch_rank, "changepercent", 1, node, 100)

    all_data = gainers_data + losers_data
    # 去重
    seen = set()
    uniq = []
    for item in all_data:
        k = item.get("symbol", "")
        if k not in seen:
            seen.add(k)
            uniq.append(item)

    # 统计
    up10   = sum(1 for x in uniq if _safe_float(x.get("changepercent")) >= 9.9)
    up5    = sum(1 for x in uniq if 5 <= _safe_float(x.get("changepercent")) < 9.9)
    up1    = sum(1 for x in uniq if 1 <= _safe_float(x.get("changepercent")) < 5)
    flat   = sum(1 for x in uniq if -1 < _safe_float(x.get("changepercent")) < 1)
    down1  = sum(1 for x in uniq if -5 < _safe_float(x.get("changepercent")) <= -1)
    down5  = sum(1 for x in uniq if -9.9 < _safe_float(x.get("changepercent")) <= -5)
    down10 = sum(1 for x in uniq if _safe_float(x.get("changepercent")) <= -9.9)

    total_up   = up10 + up5 + up1
    total_down = down1 + down5 + down10

    # 涨停/跌停代表股
    zt_samples = [f"{x.get('name')}({x.get('changepercent')}%)" for x in gainers_data[:3] if _safe_float(x.get("changepercent")) >= 9.9]
    dt_samples = [f"{x.get('name')}({x.get('changepercent')}%)" for x in losers_data[:3] if _safe_float(x.get("changepercent")) <= -9.9]

    lines = [
        f"【{market_name}市场情绪】{ts}",
        f"（注：基于样本统计，涨停/跌停数为估算）",
        "",
        f"  📈 上涨：{total_up} 只",
        f"    ├ 涨停(≥9.9%)：{up10} 只  {', '.join(zt_samples) if zt_samples else ''}",
        f"    ├ 大涨(5-9.9%)：{up5} 只",
        f"    └ 小涨(1-5%)：{up1} 只",
        f"  📊 平盘(<1%)：{flat} 只",
        f"  📉 下跌：{total_down} 只",
        f"    ├ 小跌(1-5%)：{down1} 只",
        f"    ├ 大跌(5-9.9%)：{down5} 只",
        f"    └ 跌停(≥9.9%)：{down10} 只  {', '.join(dt_samples) if dt_samples else ''}",
        "",
    ]

    # 情绪判断
    ratio = total_up / max(total_up + total_down, 1)
    if ratio >= 0.65:
        sentiment = "🔥 强势（赚钱效应明显）"
    elif ratio >= 0.50:
        sentiment = "😊 偏多（市场情绪尚可）"
    elif ratio >= 0.40:
        sentiment = "😐 平衡（多空分歧）"
    else:
        sentiment = "❄️ 弱势（亏钱效应为主）"

    lines.append(f"  综合情绪：{sentiment}")
    lines.append(f"  上涨比例：{ratio:.1%}")

    log.info("market_sentiment_fetched", market=market, up=total_up, down=total_down, zt=up10, dt=down10)
    return ToolResult(content="\n".join(lines))


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default
