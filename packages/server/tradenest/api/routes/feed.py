"""
================================================================================
文件：tradenest/api/routes/feed.py
作用：右侧面板"快讯""外盘"等聚合接口
================================================================================

接口列表：
  GET /api/news/feed?limit=20         多源快讯聚合（财联社+东财+新浪）
  GET /api/markets/global             完整外盘指数（美股/港股/欧洲/日韩/汇率/大宗）
================================================================================
"""

from __future__ import annotations
import asyncio
import requests
import structlog
from fastapi import APIRouter, Query

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api", tags=["feed"])


# ───────────── 快讯聚合 ─────────────
@router.get("/news/feed")
async def news_feed(limit: int = Query(default=20, ge=5, le=50)):
    """聚合多个新闻源，按时间倒序返回。"""
    return await asyncio.get_event_loop().run_in_executor(None, _fetch_feed, limit)


def _fetch_feed(limit: int) -> dict:
    items: list[dict] = []
    
    # 1. 财联社电报【面向全市场】
    try:
        import akshare as ak
        df = ak.stock_info_global_cls(symbol="全部")
        if df is not None and len(df) > 0:
            for _, r in df.head(limit).iterrows():
                title = str(r.get("标题", "")).strip()
                content = str(r.get("内容", ""))
                if not title:
                    title = content[:60]
                items.append({
                    "source": "财联社",
                    "time": f"{r.get('发布日期','')} {r.get('发布时间','')}".strip(),
                    "title": title,
                    "content": content[:300],
                    "url": "",
                })
    except Exception as e:
        logger.warning("cls_news_failed", error=str(e))
    
    # 2. 东方财富快讯（实际返回字段是 tag/summary/pub_time/url）
    try:
        import akshare as ak
        df = ak.stock_news_main_cx()
        if df is not None and len(df) > 0:
            for _, r in df.head(min(10, limit)).iterrows():
                summary = str(r.get("summary", ""))
                tag = str(r.get("tag", "")).strip()
                # 标题优先 summary（内容摘要），tag 是分类标签不适合当标题
                title = summary[:80] if summary else tag
                items.append({
                    "source": f"东方财富·{tag}" if tag else "东方财富",
                    "time": str(r.get("pub_time", "") or r.get("发布时间", "")),
                    "title": title,
                    "content": summary[:300],
                    "url": str(r.get("url", "")),
                })
    except Exception as e:
        logger.warning("em_news_failed", error=str(e))
    
    # 按时间排序（粗略）
    items.sort(key=lambda x: x.get("time", ""), reverse=True)
    return {"items": items[:limit], "count": len(items)}


# ───────────── 外盘 ─────────────
@router.get("/markets/global")
async def markets_global():
    """返回完整全球市场数据：美股/港股/欧洲/日韩/汇率/大宗。"""
    return await asyncio.get_event_loop().run_in_executor(None, _fetch_global_markets)


def _fetch_global_markets() -> dict:
    """从新浪外盘接口拉数据，分组返回。"""
    # 新浪外盘代码（可调整）
    SYMBOLS = {
        "美股": [
            ("int_dji",  "道琼斯"),
            ("int_nasdaq", "纳斯达克"),
            ("int_sp500",  "标普500"),
        ],
        "港股": [
            ("int_hangseng", "恒生指数"),
        ],
        "日韩": [
            ("int_nikkei",   "日经225"),
            ("b_TXIC",       "韩国KOSPI"),  # 可能不可用
        ],
        "欧洲": [
            ("b_FTSE",  "富时100"),
            ("b_GDAXI", "德国DAX"),
            ("b_FCHI",  "法国CAC40"),
        ],
        "汇率": [
            ("fx_susdcny", "美元/人民币"),
            ("fx_seurcny", "欧元/人民币"),
            ("fx_shkdcny", "港币/人民币"),
            ("fx_sjpycny", "日元/人民币"),
        ],
        "大宗": [
            ("hf_GC",   "黄金(COMEX)"),
            ("hf_SI",   "白银"),
            ("hf_CL",   "原油(WTI)"),
            ("hf_OIL",  "布伦特原油"),
        ],
    }
    
    result: dict[str, list] = {}
    all_codes = []
    for items in SYMBOLS.values():
        all_codes.extend([c for c, _ in items])
    
    try:
        url = f"https://hq.sinajs.cn/list={','.join(all_codes)}"
        r = requests.get(url, headers={"Referer": "https://finance.sina.com.cn/"}, timeout=8)
        text = r.text
        
        # 解析新浪格式
        data: dict[str, list[str]] = {}
        for line in text.split("\n"):
            if "=" not in line or '"' not in line:
                continue
            key = line.split("=")[0].strip().replace("var hq_str_", "")
            val = line.split('"')[1] if '"' in line else ""
            data[key] = val.split(",")
        
        # 按分组组装
        for group, items in SYMBOLS.items():
            result[group] = []
            for code, name in items:
                parts = data.get(code, [])
                if not parts or len(parts) < 4:
                    continue
                # 不同类型字段不同——尽量提取价格、涨跌额、涨跌幅
                price = chg = pct = None
                if code.startswith("int_"):
                    # 新浪国际指数：[0]name [1]price [2]chg [3]pct ...
                    try:
                        price = float(parts[1])
                        chg = float(parts[2])
                        pct = float(parts[3])
                    except Exception:
                        pass
                elif code.startswith("hf_"):
                    # 期货：[0]开盘 [1]highest [2]最低 [3]最新价 [4]结算 [5]昨收 [6]涨跌额 [7]涨跌幅 ...
                    try:
                        price = float(parts[3] or parts[0] or 0)
                        prev = float(parts[5] or 0)
                        if prev:
                            chg = round(price - prev, 4)
                            pct = round((price - prev) / prev * 100, 2)
                    except Exception:
                        pass
                elif code.startswith("fx_"):
                    # 外汇：[0]time [1]bid [2]ask [3]?? [4]high [5]low [6]??[7]??[8]price...
                    try:
                        # 新浪外汇格式简化处理：取第 8 位 price
                        price = float(parts[8] or parts[1] or 0)
                        prev = float(parts[5] or 0) if len(parts) > 5 else 0
                        if prev:
                            chg = round(price - prev, 4)
                            pct = round((price - prev) / prev * 100, 2)
                    except Exception:
                        pass
                elif code.startswith("b_"):
                    # 海外指数：[0]price [1]chg [2]pct ...
                    try:
                        price = float(parts[0] or 0)
                        chg = float(parts[1] or 0)
                        pct = float(parts[2] or 0)
                    except Exception:
                        pass
                
                if price is None or price == 0:
                    continue
                result[group].append({
                    "code": code,
                    "name": name,
                    "price": round(price, 2),
                    "chg": chg if chg is not None else 0,
                    "pct": pct if pct is not None else 0,
                    "up": (chg or 0) >= 0,
                })
    except Exception as e:
        logger.warning("global_markets_failed", error=str(e))
    
    return {"groups": result}
