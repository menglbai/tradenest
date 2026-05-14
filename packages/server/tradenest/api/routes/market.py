"""
================================================================================
文件：tradenest/api/routes/market.py
作用：行情相关 REST 接口
================================================================================

接口列表：
  GET  /api/market-bar          顶部行情条数据（A股三大指数 + 外盘）
  GET  /api/market-bar/a-share  仅 A 股指数
  GET  /api/market-bar/global   仅外盘
================================================================================
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["market"])

# ── 新浪行情接口（稳定，不反爬）──
SINA_URL = "https://hq.sinajs.cn/list={symbols}"
SINA_HEADERS = {"Referer": "https://finance.sina.com.cn/"}

# A 股指数 symbol → 中文名
A_SHARE_SYMBOLS = {
    "s_sh000001": "上证指数",
    "s_sz399001": "深证成指",
    "s_sz399006": "创业板指",
    "s_sh000300": "沪深300",
    "s_sh000016": "上证50",
}

# 外盘 symbol → 中文名
GLOBAL_SYMBOLS = {
    "int_dji":    "道琼斯",
    "int_nasdaq": "纳斯达克",
    "int_sp500":  "标普500",
    "int_hangseng": "恒生指数",
    "int_nikkei": "日经225",
    "fx_usdcnh":  "美元/人民币",
    "hf_GC":      "黄金",
    "hf_CL":      "原油(WTI)",
}

# 缓存（60 秒，避免频繁请求）
_cache: dict[str, Any] = {}
_cache_ts: float = 0
CACHE_TTL = 60


async def _fetch_sina(symbols: dict[str, str]) -> list[dict[str, Any]]:
    """从新浪行情接口拉一批 symbol，返回解析后的列表。"""
    sym_str = ",".join(symbols.keys())
    result: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                f"https://hq.sinajs.cn/list={sym_str}",
                headers=SINA_HEADERS,
            )
            text = resp.text
    except Exception as e:
        return [{"symbol": k, "name": v, "error": str(e)} for k, v in symbols.items()]

    for line in text.splitlines():
        if '"' not in line or "=" not in line:
            continue
        try:
            key = line.split("=")[0].strip().replace("var hq_str_", "")
            name = symbols.get(key, key)
            val_str = line.split('"')[1]
            parts = val_str.split(",")

            # A 股指数格式：名称,现价,涨跌额,涨跌幅,成交量,成交额
            if key.startswith("s_"):
                if len(parts) < 4:
                    continue
                price = parts[1]
                chg   = parts[2]
                pct   = parts[3]
                result.append({
                    "symbol": key,
                    "name": name,
                    "price": price,
                    "change": chg,
                    "change_pct": pct,
                    "up": float(pct) >= 0 if pct else None,
                    "type": "a_share",
                })

            # 外盘格式：名称,现价,涨跌额,涨跌幅,%,...
            elif key.startswith("int_") or key.startswith("fx_") or key.startswith("hf_"):
                # 尝试两种格式
                if len(parts) >= 4 and parts[1]:
                    price = parts[1]
                    chg   = parts[2]
                    pct   = parts[3].rstrip('%') if parts[3] else '0'
                elif len(parts) >= 2:
                    price = parts[0]
                    chg   = parts[1] if len(parts) > 1 else '0'
                    pct   = parts[2].rstrip('%') if len(parts) > 2 else '0'
                else:
                    continue
                if not price or not price.replace('.','').replace('-','').isdigit():
                    continue
                result.append({
                    "symbol": key,
                    "name": name,
                    "price": price,
                    "change": chg,
                    "change_pct": pct,
                    "up": float(pct) >= 0 if pct else None,
                    "type": "global",
                })
        except Exception:
            continue

    return result


@router.get("/market-bar")
async def market_bar():
    """顶部行情条：A 股三大指数 + 主要外盘，带 60s 缓存。"""
    global _cache, _cache_ts
    now = time.time()
    if _cache and now - _cache_ts < CACHE_TTL:
        return _cache

    all_symbols = {**A_SHARE_SYMBOLS, **GLOBAL_SYMBOLS}
    items = await _fetch_sina(all_symbols)

    data = {
        "items": items,
        "updated_at": now,
        "cache_ttl": CACHE_TTL,
    }
    _cache = data
    _cache_ts = now
    return data


@router.get("/market-bar/a-share")
async def market_bar_a_share():
    """仅 A 股指数。"""
    items = await _fetch_sina(A_SHARE_SYMBOLS)
    return {"items": items, "updated_at": time.time()}


@router.get("/market-bar/global")
async def market_bar_global():
    """仅外盘行情。"""
    items = await _fetch_sina(GLOBAL_SYMBOLS)
    return {"items": items, "updated_at": time.time()}
