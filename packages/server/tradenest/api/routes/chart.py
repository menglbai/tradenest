"""
================================================================================
文件：tradenest/api/routes/chart.py
作用：K线图数据接口
================================================================================

接口列表：
  GET /api/chart/kline?code=600519&days=30&adjust=qfq
    返回 ECharts K 线图所需格式的 OHLCV 数据

  GET /api/chart/kline/ma?code=600519&days=60
    返回 K 线 + 均线（MA5/MA10/MA20/MA60）

数据来源：AkShare（东方财富），降级到 mock 数据
================================================================================
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/chart", tags=["chart"])


@router.get("/kline")
async def kline(
    code: str = Query(..., description="股票代码，如 600519"),
    days: int = Query(default=120, ge=5, le=2000, description="点数（根据 period 涵义不同：日/周/月）"),
    period: str = Query(default="daily", description="周期：daily日/weekly周/monthly月"),
    adjust: str = Query(default="qfq", description="复权方式：qfq前复权/hfq后复权/空不复权"),
):
    """
    返回 ECharts candlestick 格式的 K 线数据。

    响应结构：
    {
      "code": "600519",
      "name": "贵州茅台",
      "days": 60,
      "dates":  ["2025-01-02", ...],          // x 轴日期
      "ohlcv":  [[open,close,low,high,vol], ...],  // ECharts candlestick 格式
      "volumes": [vol, ...],                   // 成交量
      "ma": { "ma5": [...], "ma10": [...], "ma20": [...], "ma60": [...] }
    }
    """
    import asyncio
    return await asyncio.get_event_loop().run_in_executor(None, _fetch_kline, code, days, adjust, period)


def _fetch_kline(code: str, days: int, adjust: str, period: str = "daily") -> dict:
    """同步拉 K 线数据（在线程池中执行，不阻塞事件循环）。"""
    import datetime
    try:
        import akshare as ak
        import pandas as pd

        end = datetime.date.today()
        # 根据周期倒推起始时间：月线拉 days*40，周线拉 days*8，日线拉 days+60
        if period == "monthly":
            start = end - datetime.timedelta(days=days * 35 + 200)
        elif period == "weekly":
            start = end - datetime.timedelta(days=days * 8 + 100)
        else:
            start = end - datetime.timedelta(days=days + 80)

        df = ak.stock_zh_a_hist(
            symbol=code,
            period=period if period in ("daily", "weekly", "monthly") else "daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust=adjust if adjust else "",
        )

        # 统一列名
        col_map = {
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "成交额": "amount", "振幅": "amplitude",
            "涨跌幅": "pct_change", "涨跌额": "price_change",
        }
        df = df.rename(columns=col_map)
        df = df.sort_values("date").tail(days)
        df = df.reset_index(drop=True)

        name = _get_stock_name(code)

        dates   = df["date"].astype(str).tolist()
        # ECharts candlestick 格式：[open, close, low, high]
        ohlcv   = df[["open", "close", "low", "high"]].round(2).values.tolist()
        volumes = df["volume"].astype(int).tolist()
        pct     = df.get("pct_change", pd.Series([0]*len(df))).round(2).tolist()

        # 均线
        closes = df["close"].tolist()
        ma = {
            "ma5":  _calc_ma(closes, 5),
            "ma10": _calc_ma(closes, 10),
            "ma20": _calc_ma(closes, 20),
            "ma60": _calc_ma(closes, 60),
        }

        return {
            "code": code,
            "name": name,
            "days": len(dates),
            "period": period,
            "adjust": adjust,
            "dates": dates,
            "ohlcv": ohlcv,
            "volumes": volumes,
            "pct_change": pct,
            "ma": ma,
        }

    except Exception as e:
        # 降级：返回 mock 数据（网络不通时前端仍能显示）
        return _mock_kline(code, days, str(e))


def _calc_ma(closes: list[float], n: int) -> list[float | None]:
    """计算 N 日均线，前 n-1 天返回 None。"""
    result: list[float | None] = []
    for i in range(len(closes)):
        if i < n - 1:
            result.append(None)
        else:
            avg = sum(closes[i - n + 1: i + 1]) / n
            result.append(round(avg, 2))
    return result


def _get_stock_name(code: str) -> str:
    """尝试从 AkShare 拿股票名称，失败则用代码。"""
    try:
        import akshare as ak
        info = ak.stock_individual_info_em(symbol=code)
        name_rows = info[info.iloc[:, 0] == "股票简称"]
        if not name_rows.empty:
            return str(name_rows.iloc[0, 1])
    except Exception:
        pass
    return code


def _mock_kline(code: str, days: int, reason: str = "") -> dict:
    """生成 mock K 线数据（无网络时降级使用）。"""
    import datetime, random, math
    base = 1344.0
    dates, ohlcv, volumes = [], [], []
    closes = []
    today = datetime.date.today()

    for i in range(days):
        d = today - datetime.timedelta(days=days - i)
        if d.weekday() >= 5:  # 跳过周末
            continue
        t = i / days * 2 * math.pi
        noise = random.gauss(0, 0.015)
        close = round(base * (1 + 0.05 * math.sin(t) + noise), 2)
        open_ = round(close * (1 + random.gauss(0, 0.005)), 2)
        high  = round(max(open_, close) * (1 + abs(random.gauss(0, 0.008))), 2)
        low   = round(min(open_, close) * (1 - abs(random.gauss(0, 0.008))), 2)
        vol   = int(random.gauss(500000, 100000))
        dates.append(str(d))
        ohlcv.append([open_, close, low, high])
        volumes.append(vol)
        closes.append(close)
        base = close

    ma = {
        "ma5":  _calc_ma(closes, 5),
        "ma10": _calc_ma(closes, 10),
        "ma20": _calc_ma(closes, 20),
        "ma60": _calc_ma(closes, 60),
    }
    return {
        "code": code, "name": f"{code}(mock)", "days": len(dates),
        "period": "daily", "adjust": "mock", "dates": dates, "ohlcv": ohlcv,
        "volumes": volumes, "pct_change": [0]*len(dates), "ma": ma,
        "_mock": True, "_reason": reason,
    }
