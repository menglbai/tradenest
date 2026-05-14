"""
================================================================================
文件：tradenest/api/routes/chart.py
作用：K线图数据接口

数据源优先级：
  1. 同花顺（d.10jqka.com.cn）—— 日/周/月/季/年，含股票名称
  2. 新浪财经（money.finance.sina.com.cn）—— 降级备用
  3. Mock —— 两个数据源都失败时降级

接口：
  GET /api/chart/kline?code=601778&days=120&period=daily&adjust=qfq
    period: daily(日) / weekly(周) / monthly(月)
    adjust: qfq(前复权) / hfq(后复权) / ''(不复权)
    返回 ECharts candlestick 所需格式

================================================================================
"""
from __future__ import annotations

import asyncio
import re
import json as _json

import requests
import structlog
from fastapi import APIRouter, Query

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/chart", tags=["chart"])

# 同花顺 period → URL 代码
_THS_PERIOD = {
    "daily":   "01",   # 日线
    "weekly":  "11",   # 周线
    "monthly": "21",   # 月线
}

# 新浪 period → scale
_SINA_SCALE = {
    "daily":   "240",
    "weekly":  "1680",
    "monthly": "7200",
}

_HEADERS_THS = {
    "Referer": "https://stockpage.10jqka.com.cn/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}
_HEADERS_SINA = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": "Mozilla/5.0",
}


@router.get("/kline")
async def kline(
    code:   str = Query(..., description="股票代码，如 601778 / 600519"),
    days:   int = Query(default=120, ge=5, le=2000, description="返回K线条数"),
    period: str = Query(default="daily",  description="日线daily/周线weekly/月线monthly"),
    adjust: str = Query(default="qfq",    description="前复权qfq/后复权hfq/不复权空"),
):
    """返回 ECharts candlestick 格式 K 线数据（同花顺 → 新浪 → Mock 三级降级）。"""
    return await asyncio.get_event_loop().run_in_executor(
        None, _fetch_kline, code, days, period, adjust
    )


# ─────────────────── 主逻辑 ───────────────────

def _fetch_kline(code: str, days: int, period: str, adjust: str) -> dict:
    period = period if period in _THS_PERIOD else "daily"

    # 1. 尝试同花顺
    try:
        return _fetch_ths(code, days, period, adjust)
    except Exception as e:
        logger.warning("ths_kline_failed", code=code, period=period, error=str(e))

    # 2. 降级新浪
    try:
        return _fetch_sina(code, days, period, adjust)
    except Exception as e:
        logger.warning("sina_kline_failed", code=code, period=period, error=str(e))

    # 3. 降级 Mock
    return _mock_kline(code, days, period)


# ─────────────────── 同花顺 ───────────────────

def _fetch_ths(code: str, days: int, period: str, adjust: str) -> dict:
    """
    同花顺历史K线接口。
    URL 格式：https://d.10jqka.com.cn/v6/line/hs_{code}/{period_code}/last{days}.js
    复权：qfq → /v6/line/hs_{code}/{period_code}/last{days}.js（默认前复权）
          hfq → /v6/line/hb_{code}/...（后复权，部分代码）
          不复权 → /v6/line/hs_{code}/{period_code}/last{days}.js（同，接口本身前复权）
    数据格式：JSONP，data字段 = "日期,开盘,最高,最低,收盘,成交量,成交额,涨幅,,,0;" 分号分隔
    """
    period_code = _THS_PERIOD[period]
    count = min(days + 80, 2000)   # 多拉一些用于均线计算

    # 复权前缀：hs=前复权，hb=后复权
    prefix = "hb" if adjust == "hfq" else "hs"
    url = f"https://d.10jqka.com.cn/v6/line/{prefix}_{code}/{period_code}/last{count}.js"

    r = requests.get(url, headers=_HEADERS_THS, timeout=12)
    r.raise_for_status()

    raw = re.sub(r"^[^(]+\(", "", r.text.strip()).rstrip(")")
    d = _json.loads(raw)

    name = d.get("name", code)
    data_str = d.get("data", "")
    if not data_str:
        raise ValueError("empty data from THS")

    rows = [row.split(",") for row in data_str.strip(";").split(";") if row]
    # 格式：[日期, 开盘, 最高, 最低, 收盘, 成交量, 成交额, 涨跌幅, ?, ?, ?]
    rows = rows[-days:]  # 取最近 days 条

    dates, ohlcv, volumes, pct_change = [], [], [], []
    closes = []
    for row in rows:
        if len(row) < 7:
            continue
        try:
            date   = _fmt_date(row[0])
            open_  = float(row[1])
            high   = float(row[2])
            low    = float(row[3])
            close  = float(row[4])
            volume = int(float(row[5]))
            pct    = float(row[7]) if len(row) > 7 and row[7] else 0.0
        except (ValueError, IndexError):
            continue
        dates.append(date)
        ohlcv.append([open_, close, low, high])   # ECharts: open/close/low/high
        volumes.append(volume)
        closes.append(close)
        pct_change.append(round(pct, 2))

    if not dates:
        raise ValueError("no valid rows from THS")

    ma = {
        "ma5":  _calc_ma(closes, 5),
        "ma10": _calc_ma(closes, 10),
        "ma20": _calc_ma(closes, 20),
        "ma60": _calc_ma(closes, 60),
    }

    return {
        "code": code, "name": name, "days": len(dates),
        "period": period, "adjust": adjust,
        "dates": dates, "ohlcv": ohlcv,
        "volumes": volumes, "pct_change": pct_change,
        "ma": ma, "_source": "ths",
    }


# ─────────────────── 新浪（降级） ───────────────────

def _fetch_sina(code: str, days: int, period: str, adjust: str) -> dict:
    """
    新浪历史K线接口（降级备用）。
    格式：{'day':'2026-05-14','open':'7.24','high':'7.85','low':'7.16','close':'7.52','volume':'963618120'}
    """
    scale = _SINA_SCALE[period]
    # 新浪代码前缀：沪市 sh / 深市 sz
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    sym = f"{prefix}{code}"

    count = min(days + 80, 1000)
    url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    r = requests.get(url, params={"symbol": sym, "scale": scale, "ma": "no", "datalen": count},
                     headers=_HEADERS_SINA, timeout=12)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        raise ValueError("empty from sina")

    rows = rows[-days:]
    dates, ohlcv, volumes, closes, pct_change = [], [], [], [], []
    prev_close = None
    for row in rows:
        try:
            date   = str(row["day"])
            open_  = float(row["open"])
            high   = float(row["high"])
            low    = float(row["low"])
            close  = float(row["close"])
            volume = int(float(row.get("volume", 0)))
        except (KeyError, ValueError):
            continue
        pct = round((close - prev_close) / prev_close * 100, 2) if prev_close else 0.0
        prev_close = close
        dates.append(date)
        ohlcv.append([open_, close, low, high])
        volumes.append(volume)
        closes.append(close)
        pct_change.append(pct)

    # 从实时行情拿股票名称
    name = _get_name_from_tencent(code)

    ma = {
        "ma5":  _calc_ma(closes, 5),
        "ma10": _calc_ma(closes, 10),
        "ma20": _calc_ma(closes, 20),
        "ma60": _calc_ma(closes, 60),
    }
    return {
        "code": code, "name": name, "days": len(dates),
        "period": period, "adjust": "qfq",
        "dates": dates, "ohlcv": ohlcv,
        "volumes": volumes, "pct_change": pct_change,
        "ma": ma, "_source": "sina",
    }


# ─────────────────── 工具函数 ───────────────────

def _fmt_date(raw: str) -> str:
    """20260514 → 2026-05-14"""
    raw = raw.strip()
    if len(raw) == 8 and "-" not in raw:
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


def _get_name_from_tencent(code: str) -> str:
    """从腾讯行情接口获取股票名称。"""
    try:
        prefix = "sh" if code.startswith(("6", "9")) else "sz"
        r = requests.get(f"https://qt.gtimg.cn/q={prefix}{code}",
                         timeout=5, headers={"Referer": "https://finance.qq.com/"})
        parts = r.text.split("~")
        if len(parts) > 1:
            return parts[1]
    except Exception:
        pass
    return code


def _calc_ma(closes: list[float], n: int) -> list[float | None]:
    """计算 N 日均线，前 n-1 条返回 None。"""
    result: list[float | None] = []
    for i in range(len(closes)):
        if i < n - 1:
            result.append(None)
        else:
            avg = sum(closes[i - n + 1: i + 1]) / n
            result.append(round(avg, 2))
    return result


def _mock_kline(code: str, days: int, period: str) -> dict:
    """生成 Mock K 线（两个数据源都失败时降级）。"""
    import datetime, random, math
    base = 10.0
    dates, ohlcv, volumes, closes, pct_change = [], [], [], [], []
    today = datetime.date.today()
    for i in range(days):
        d = today - datetime.timedelta(days=days - i)
        if d.weekday() >= 5:
            continue
        t = i / days * 2 * math.pi
        noise = random.gauss(0, 0.015)
        close = round(base * (1 + 0.05 * math.sin(t) + noise), 2)
        open_ = round(close * (1 + random.gauss(0, 0.005)), 2)
        high  = round(max(open_, close) * (1 + abs(random.gauss(0, 0.008))), 2)
        low   = round(min(open_, close) * (1 - abs(random.gauss(0, 0.008))), 2)
        vol   = int(random.gauss(500_000, 100_000))
        pct   = round((close - base) / base * 100, 2)
        dates.append(str(d))
        ohlcv.append([open_, close, low, high])
        volumes.append(vol)
        closes.append(close)
        pct_change.append(pct)
        base = close

    return {
        "code": code, "name": f"{code}(mock)", "days": len(dates),
        "period": period, "adjust": "mock",
        "dates": dates, "ohlcv": ohlcv,
        "volumes": volumes, "pct_change": pct_change,
        "ma": {
            "ma5":  _calc_ma(closes, 5),
            "ma10": _calc_ma(closes, 10),
            "ma20": _calc_ma(closes, 20),
            "ma60": _calc_ma(closes, 60),
        },
        "_source": "mock",
        "_mock": True,
    }
