"""
================================================================================
文件：tradenest/api/routes/portfolio.py
作用：持仓管理 + 关注列表 API
================================================================================

持仓功能（同花顺标准）：
  - 分批买入，自动加权均价
  - 卖出操作，计算实现盈亏
  - 历史持仓归档
  - 手续费记录（默认万3）
  - 今日盈亏 / 总盈亏 / 总市值 / 总成本

关注功能：
  - 独立于持仓，只跟踪行情
  - 支持备注，一键转入持仓

接口：
  GET  /api/portfolio/summary          总览（总资产/总收益/今日盈亏）
  GET  /api/portfolio/positions        持仓列表（含实时行情）
  POST /api/portfolio/positions        新建持仓（首次买入）
  GET  /api/portfolio/positions/{id}   持仓详情（含交易流水）
  POST /api/portfolio/positions/{id}/buy    加仓
  POST /api/portfolio/positions/{id}/sell   减仓/清仓
  DELETE /api/portfolio/positions/{id}      删除持仓（含流水）

  GET  /api/portfolio/history          历史持仓（已清仓）
  GET  /api/portfolio/watchlist        关注列表（含实时行情）
  POST /api/portfolio/watchlist        添加关注
  DELETE /api/portfolio/watchlist/{code}   删除关注
  POST /api/portfolio/watchlist/{code}/to-position  转为持仓
================================================================================
"""

from __future__ import annotations

import time
from typing import Any

import requests
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

# ══════════════════════════════════════════════════════
#  数据库初始化
# ══════════════════════════════════════════════════════

def init_portfolio_tables() -> None:
    """创建持仓相关表（幂等）。"""
    from tradenest.db.store import get_db
    db = get_db()
    db.executescript("""
        -- 持仓主表
        CREATE TABLE IF NOT EXISTS positions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code        TEXT    NOT NULL,
            name        TEXT    NOT NULL DEFAULT '',
            status      TEXT    NOT NULL DEFAULT 'holding'  -- holding | closed
                            CHECK(status IN ('holding','closed')),
            total_shares    REAL NOT NULL DEFAULT 0,
            avg_cost        REAL NOT NULL DEFAULT 0,   -- 加权均价（含手续费）
            total_cost      REAL NOT NULL DEFAULT 0,   -- 总成本（含手续费）
            realized_profit REAL NOT NULL DEFAULT 0,   -- 已实现盈亏
            note        TEXT    DEFAULT '',
            created_at  INTEGER NOT NULL,
            updated_at  INTEGER NOT NULL
        );

        -- 交易流水
        CREATE TABLE IF NOT EXISTS position_trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER NOT NULL,
            type        TEXT    NOT NULL CHECK(type IN ('buy','sell')),
            date        TEXT    NOT NULL,     -- 交易日期 YYYY-MM-DD
            price       REAL    NOT NULL,     -- 成交价
            shares      REAL    NOT NULL,     -- 成交数量（股）
            fee         REAL    NOT NULL DEFAULT 0,  -- 手续费（元）
            note        TEXT    DEFAULT '',
            created_at  INTEGER NOT NULL,
            FOREIGN KEY (position_id) REFERENCES positions(id) ON DELETE CASCADE
        );

        -- 关注列表（独立于 watchlist 表，更丰富）
        CREATE TABLE IF NOT EXISTS portfolio_watchlist (
            code        TEXT    PRIMARY KEY,
            name        TEXT    NOT NULL DEFAULT '',
            note        TEXT    DEFAULT '',
            added_at    INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_trades_position ON position_trades(position_id, date);
        CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
    """)
    db.commit()


# ══════════════════════════════════════════════════════
#  实时行情获取（腾讯接口）
# ══════════════════════════════════════════════════════

def _get_quotes(codes: list[str]) -> dict[str, dict]:
    """批量获取实时行情，返回 {code: {price, prev_close, chg, pct, name, ...}}。"""
    if not codes:
        return {}
    sym_list = []
    for c in codes:
        if c.startswith("6"):
            sym_list.append("sh" + c)
        elif c.startswith(("0", "3")):
            sym_list.append("sz" + c)
        elif c.startswith(("4", "8")):
            sym_list.append("bj" + c)
        else:
            sym_list.append("sh" + c)

    try:
        r = requests.get(
            f"https://qt.gtimg.cn/q={','.join(sym_list)}",
            headers={"Referer": "https://gu.qq.com/"},
            timeout=8,
        )
        result: dict[str, dict] = {}
        for line in r.text.strip().split("\n"):
            if "~" not in line or '"' not in line:
                continue
            try:
                inner = line.split('"')[1]
                parts = inner.split("~")
                code = parts[2]
                result[code] = {
                    "name":       parts[1],
                    "price":      float(parts[3] or 0),
                    "prev_close": float(parts[4] or 0),
                    "open":       float(parts[5] or 0),
                    "high":       float(parts[33] or 0),
                    "low":        float(parts[34] or 0),
                    "volume":     int(float(parts[6] or 0)),
                    "amount":     float(parts[37] or 0),
                    "chg":        float(parts[31] or 0),
                    "pct":        float(parts[32] or 0),
                }
            except Exception:
                continue
        return result
    except Exception as e:
        logger.warning("quote_failed", error=str(e))
        return {}


def _calc_today_profit(position: dict, quote: dict) -> float:
    """今日盈亏 = (当前价 - 昨收) × 持仓数量。"""
    if not quote:
        return 0.0
    return round((quote["price"] - quote["prev_close"]) * position["total_shares"], 2)


def _calc_total_profit(position: dict, quote: dict) -> tuple[float, float]:
    """总盈亏（绝对值，百分比）。"""
    if not quote or position["total_cost"] <= 0:
        return 0.0, 0.0
    market_value = quote["price"] * position["total_shares"]
    profit = round(market_value - position["total_cost"] + position["realized_profit"], 2)
    pct = round(profit / position["total_cost"] * 100, 2) if position["total_cost"] > 0 else 0.0
    return profit, pct


# ══════════════════════════════════════════════════════
#  总览
# ══════════════════════════════════════════════════════

@router.get("/summary")
def get_summary():
    """总览：总市值 / 总成本 / 总盈亏 / 今日盈亏。"""
    from tradenest.db.store import get_db
    db = get_db()
    rows = db.execute(
        "SELECT id, code, name, total_shares, avg_cost, total_cost, realized_profit "
        "FROM positions WHERE status='holding' AND total_shares > 0"
    ).fetchall()

    if not rows:
        return {
            "total_market_value": 0,
            "total_cost": 0,
            "total_profit": 0,
            "total_profit_pct": 0,
            "today_profit": 0,
            "position_count": 0,
        }

    codes = [r["code"] for r in rows]
    quotes = _get_quotes(codes)

    total_market_value = 0.0
    total_cost = 0.0
    total_realized = 0.0
    today_profit = 0.0

    for r in rows:
        pos = dict(r)
        q = quotes.get(r["code"], {})
        if q.get("price"):
            mv = q["price"] * r["total_shares"]
        else:
            mv = r["avg_cost"] * r["total_shares"]
        total_market_value += mv
        total_cost += r["total_cost"]
        total_realized += r["realized_profit"]
        today_profit += _calc_today_profit(pos, q)

    total_unrealized = total_market_value - total_cost
    total_profit = round(total_unrealized + total_realized, 2)
    total_profit_pct = round(total_profit / total_cost * 100, 2) if total_cost > 0 else 0.0

    return {
        "total_market_value": round(total_market_value, 2),
        "total_cost":         round(total_cost, 2),
        "total_profit":       total_profit,
        "total_profit_pct":   total_profit_pct,
        "today_profit":       round(today_profit, 2),
        "position_count":     len(rows),
    }


# ══════════════════════════════════════════════════════
#  持仓列表
# ══════════════════════════════════════════════════════

@router.get("/positions")
def list_positions():
    """持仓列表，含实时行情和盈亏计算。"""
    from tradenest.db.store import get_db
    db = get_db()
    rows = db.execute(
        "SELECT id, code, name, total_shares, avg_cost, total_cost, realized_profit, note, created_at "
        "FROM positions WHERE status='holding' ORDER BY updated_at DESC"
    ).fetchall()

    if not rows:
        return {"positions": []}

    codes = list({r["code"] for r in rows})
    quotes = _get_quotes(codes)

    positions = []
    for r in rows:
        pos = dict(r)
        q = quotes.get(r["code"], {})
        price      = q.get("price") or r["avg_cost"]
        market_val = round(price * r["total_shares"], 2)
        profit, profit_pct = _calc_total_profit(pos, q)
        today_p = _calc_today_profit(pos, q)

        positions.append({
            **pos,
            "current_price":  price,
            "market_value":   market_val,
            "total_profit":   profit,
            "profit_pct":     profit_pct,
            "today_profit":   today_p,
            "today_pct":      round(q.get("pct", 0), 2),
            "quote":          q,
        })
    return {"positions": positions}


# ══════════════════════════════════════════════════════
#  新建持仓（首次买入）
# ══════════════════════════════════════════════════════

class BuyRequest(BaseModel):
    code:   str
    name:   str = ""
    date:   str             # YYYY-MM-DD
    price:  float
    shares: float           # 股数
    fee:    float = 0.0     # 手续费（元）
    note:   str = ""


@router.post("/positions")
def create_position(req: BuyRequest):
    """新建持仓（首次买入）。"""
    from tradenest.db.store import get_db
    if req.price <= 0 or req.shares <= 0:
        raise HTTPException(status_code=400, detail="价格和数量必须大于0")

    # 自动补全股票名称
    name = req.name
    if not name:
        q = _get_quotes([req.code])
        name = q.get(req.code, {}).get("name", req.code)

    total_cost = round(req.price * req.shares + req.fee, 4)
    avg_cost   = round(total_cost / req.shares, 4)
    now = int(time.time())

    db = get_db()
    # 检查是否已有同代码持仓
    existing = db.execute(
        "SELECT id FROM positions WHERE code=? AND status='holding'", (req.code,)
    ).fetchone()
    if existing:
        raise HTTPException(status_code=400, detail=f"已有 {req.code} 的持仓，请使用加仓接口")

    cur = db.execute(
        "INSERT INTO positions(code,name,status,total_shares,avg_cost,total_cost,realized_profit,note,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,0,?,?,?)",
        (req.code, name, "holding", req.shares, avg_cost, total_cost, req.note, now, now)
    )
    pos_id = cur.lastrowid
    db.execute(
        "INSERT INTO position_trades(position_id,type,date,price,shares,fee,note,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (pos_id, "buy", req.date, req.price, req.shares, req.fee, req.note, now)
    )
    db.commit()
    return {"ok": True, "position_id": pos_id, "name": name}


# ══════════════════════════════════════════════════════
#  持仓详情（含流水）
# ══════════════════════════════════════════════════════

@router.get("/positions/{position_id}")
def get_position(position_id: int):
    from tradenest.db.store import get_db
    db = get_db()
    pos = db.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
    if not pos:
        raise HTTPException(status_code=404, detail="持仓不存在")

    trades = db.execute(
        "SELECT * FROM position_trades WHERE position_id=? ORDER BY date, id",
        (position_id,)
    ).fetchall()

    q = _get_quotes([pos["code"]]).get(pos["code"], {})
    pos_dict = dict(pos)
    profit, profit_pct = _calc_total_profit(pos_dict, q)

    return {
        "position": {
            **pos_dict,
            "current_price": q.get("price", pos["avg_cost"]),
            "market_value":  round(q.get("price", pos["avg_cost"]) * pos["total_shares"], 2),
            "total_profit":  profit,
            "profit_pct":    profit_pct,
            "today_profit":  _calc_today_profit(pos_dict, q),
            "today_pct":     round(q.get("pct", 0), 2),
            "quote": q,
        },
        "trades": [dict(t) for t in trades],
    }


# ══════════════════════════════════════════════════════
#  加仓
# ══════════════════════════════════════════════════════

@router.post("/positions/{position_id}/buy")
def add_buy(position_id: int, req: BuyRequest):
    """加仓：更新加权均价 = (旧成本 + 新成本) / 总股数。"""
    from tradenest.db.store import get_db
    if req.price <= 0 or req.shares <= 0:
        raise HTTPException(status_code=400, detail="价格和数量必须大于0")

    db = get_db()
    pos = db.execute("SELECT * FROM positions WHERE id=? AND status='holding'", (position_id,)).fetchone()
    if not pos:
        raise HTTPException(status_code=404, detail="持仓不存在")

    new_cost       = req.price * req.shares + req.fee
    total_shares   = pos["total_shares"] + req.shares
    total_cost     = pos["total_cost"] + new_cost
    avg_cost       = round(total_cost / total_shares, 4)
    now = int(time.time())

    db.execute(
        "UPDATE positions SET total_shares=?, avg_cost=?, total_cost=?, updated_at=? WHERE id=?",
        (total_shares, avg_cost, total_cost, now, position_id)
    )
    db.execute(
        "INSERT INTO position_trades(position_id,type,date,price,shares,fee,note,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (position_id, "buy", req.date, req.price, req.shares, req.fee, req.note, now)
    )
    db.commit()
    return {"ok": True, "new_shares": total_shares, "new_avg_cost": avg_cost}


# ══════════════════════════════════════════════════════
#  卖出
# ══════════════════════════════════════════════════════

class SellRequest(BaseModel):
    date:   str
    price:  float
    shares: float
    fee:    float = 0.0
    note:   str = ""


@router.post("/positions/{position_id}/sell")
def add_sell(position_id: int, req: SellRequest):
    """卖出：计算本次实现盈亏，全部清仓则 status → closed。"""
    from tradenest.db.store import get_db
    if req.price <= 0 or req.shares <= 0:
        raise HTTPException(status_code=400, detail="价格和数量必须大于0")

    db = get_db()
    pos = db.execute("SELECT * FROM positions WHERE id=? AND status='holding'", (position_id,)).fetchone()
    if not pos:
        raise HTTPException(status_code=404, detail="持仓不存在")
    if req.shares > pos["total_shares"]:
        raise HTTPException(status_code=400, detail=f"卖出数量({req.shares})超过持仓({pos['total_shares']})")

    # 本次实现盈亏 = (卖出价 - 均价) × 数量 - 手续费
    this_profit    = round((req.price - pos["avg_cost"]) * req.shares - req.fee, 2)
    realized       = round(pos["realized_profit"] + this_profit, 2)

    remaining      = round(pos["total_shares"] - req.shares, 4)
    new_total_cost = round(pos["avg_cost"] * remaining, 2)  # 剩余成本（均价不变）
    new_status     = "closed" if remaining <= 0 else "holding"
    now = int(time.time())

    db.execute(
        "UPDATE positions SET total_shares=?, total_cost=?, realized_profit=?, status=?, updated_at=? WHERE id=?",
        (remaining, new_total_cost, realized, new_status, now, position_id)
    )
    db.execute(
        "INSERT INTO position_trades(position_id,type,date,price,shares,fee,note,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (position_id, "sell", req.date, req.price, req.shares, req.fee, req.note, now)
    )
    db.commit()
    return {
        "ok": True,
        "this_profit": this_profit,
        "remaining_shares": remaining,
        "status": new_status,
    }


# ══════════════════════════════════════════════════════
#  删除持仓
# ══════════════════════════════════════════════════════

@router.delete("/positions/{position_id}")
def delete_position(position_id: int):
    from tradenest.db.store import get_db
    db = get_db()
    if not db.execute("SELECT id FROM positions WHERE id=?", (position_id,)).fetchone():
        raise HTTPException(status_code=404, detail="持仓不存在")
    db.execute("DELETE FROM positions WHERE id=?", (position_id,))
    db.commit()
    return {"ok": True}


# ══════════════════════════════════════════════════════
#  历史持仓
# ══════════════════════════════════════════════════════

@router.get("/history")
def get_history():
    """已清仓的历史持仓记录。"""
    from tradenest.db.store import get_db
    db = get_db()
    rows = db.execute(
        "SELECT id,code,name,avg_cost,realized_profit,created_at,updated_at "
        "FROM positions WHERE status='closed' ORDER BY updated_at DESC LIMIT 50"
    ).fetchall()

    history = []
    for r in rows:
        trades = db.execute(
            "SELECT type,date,price,shares,fee FROM position_trades WHERE position_id=? ORDER BY date",
            (r["id"],)
        ).fetchall()
        buy_trades  = [t for t in trades if t["type"] == "buy"]
        sell_trades = [t for t in trades if t["type"] == "sell"]
        total_shares_bought = sum(t["shares"] for t in buy_trades)
        total_cost = sum(t["price"] * t["shares"] + t["fee"] for t in buy_trades)
        avg_buy    = round(total_cost / total_shares_bought, 4) if total_shares_bought else 0
        total_sell = sum(t["price"] * t["shares"] for t in sell_trades)
        avg_sell   = round(total_sell / sum(t["shares"] for t in sell_trades), 4) if sell_trades else 0
        hold_days  = 0
        if buy_trades and sell_trades:
            import datetime
            try:
                d0 = datetime.date.fromisoformat(buy_trades[0]["date"])
                d1 = datetime.date.fromisoformat(sell_trades[-1]["date"])
                hold_days = (d1 - d0).days
            except Exception:
                pass
        history.append({
            "id":              r["id"],
            "code":            r["code"],
            "name":            r["name"],
            "avg_buy_price":   avg_buy,
            "avg_sell_price":  avg_sell,
            "realized_profit": round(r["realized_profit"], 2),
            "profit_pct":      round(r["realized_profit"] / total_cost * 100, 2) if total_cost else 0,
            "hold_days":       hold_days,
            "buy_date":        buy_trades[0]["date"] if buy_trades else "",
            "sell_date":       sell_trades[-1]["date"] if sell_trades else "",
        })
    return {"history": history}


# ══════════════════════════════════════════════════════
#  关注列表
# ══════════════════════════════════════════════════════

class WatchRequest(BaseModel):
    code: str
    name: str = ""
    note: str = ""


@router.get("/watchlist")
def get_watchlist():
    """关注列表，含实时行情。"""
    from tradenest.db.store import get_db
    db = get_db()
    rows = db.execute("SELECT code, name, note, added_at FROM portfolio_watchlist ORDER BY added_at").fetchall()
    if not rows:
        return {"watchlist": []}

    codes = [r["code"] for r in rows]
    quotes = _get_quotes(codes)

    result = []
    for r in rows:
        q = quotes.get(r["code"], {})
        result.append({
            **dict(r),
            "current_price": q.get("price", 0),
            "chg":           round(q.get("chg", 0), 2),
            "pct":           round(q.get("pct", 0), 2),
            "high":          q.get("high", 0),
            "low":           q.get("low", 0),
            "open":          q.get("open", 0),
            "amount":        q.get("amount", 0),
            "up":            q.get("chg", 0) >= 0,
            "display_name":  q.get("name") or r["name"] or r["code"],
        })
    return {"watchlist": result}


@router.post("/watchlist")
def add_watchlist(req: WatchRequest):
    from tradenest.db.store import get_db
    code = req.code.strip()
    name = req.name
    if not name:
        q = _get_quotes([code])
        name = q.get(code, {}).get("name", code)
    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO portfolio_watchlist(code,name,note,added_at) VALUES(?,?,?,?)",
        (code, name, req.note, int(time.time()))
    )
    db.commit()
    return {"ok": True, "code": code, "name": name}


@router.delete("/watchlist/{code}")
def remove_watchlist(code: str):
    from tradenest.db.store import get_db
    db = get_db()
    db.execute("DELETE FROM portfolio_watchlist WHERE code=?", (code,))
    db.commit()
    return {"ok": True}


@router.post("/watchlist/{code}/to-position")
def watch_to_position(code: str, req: BuyRequest):
    """将关注转为持仓（同时删除关注记录）。"""
    from tradenest.db.store import get_db
    result = create_position(req)
    db = get_db()
    db.execute("DELETE FROM portfolio_watchlist WHERE code=?", (code,))
    db.commit()
    return result


# ══════════════════════════════════════════════════════
#  月度盈亏统计
# ══════════════════════════════════════════════════════
@router.get("/stats/monthly")
def monthly_pnl():
    """按月统计已实现盈亏（从交易流水计算）。"""
    from tradenest.db.store import get_db
    db = get_db()
    trades = db.execute(
        "SELECT t.type, t.date, t.price, t.shares, t.fee, p.avg_cost "
        "FROM position_trades t JOIN positions p ON t.position_id=p.id "
        "WHERE t.type='sell' ORDER BY t.date"
    ).fetchall()

    monthly: dict[str, float] = {}
    for t in trades:
        month = t["date"][:7]  # YYYY-MM
        profit = round((t["price"] - t["price"]) * t["shares"] - t["fee"], 2)  # 简化：已存 realized_profit
        monthly[month] = round(monthly.get(month, 0) + profit, 2)

    # 改从 positions 的 realized_profit + updated_at 推算（更准确）
    positions = db.execute(
        "SELECT realized_profit, updated_at FROM positions WHERE realized_profit != 0"
    ).fetchall()

    import datetime
    monthly2: dict[str, float] = {}
    for p in positions:
        if p["realized_profit"]:
            month = datetime.datetime.fromtimestamp(p["updated_at"]).strftime("%Y-%m")
            monthly2[month] = round(monthly2.get(month, 0) + p["realized_profit"], 2)

    sorted_months = sorted(set(list(monthly.keys()) + list(monthly2.keys())))
    return {
        "months": sorted_months or [],
        "values": [round(monthly2.get(m, 0), 2) for m in sorted_months],
    }


# ══════════════════════════════════════════════════════
#  持仓收益曲线（模拟：按K线还原每日市值）
# ══════════════════════════════════════════════════════
@router.get("/stats/equity-curve")
def equity_curve():
    """返回近30天总持仓市值变化（用当日收盘价 × 持仓数量估算）。"""
    from tradenest.db.store import get_db
    import datetime, requests as _req
    db = get_db()
    positions = db.execute(
        "SELECT code, total_shares, avg_cost FROM positions WHERE status='holding' AND total_shares>0"
    ).fetchall()
    if not positions:
        return {"dates": [], "values": [], "cost_line": 0}

    # 拉每只票近30日收盘价（同花顺）
    today = datetime.date.today()
    dates_set: set[str] = set()
    price_map: dict[str, dict[str, float]] = {}  # code → {date → close}

    for pos in positions:
        code = pos["code"]
        try:
            import time as _time
            start = (today - datetime.timedelta(days=40)).strftime("%Y%m%d")
            url = f"https://d.10jqka.com.cn/v6/line/hs_{code}/01/{today.strftime('%Y%m%d')}.js"
            r = _req.get(url, headers={"Referer": "https://stockpage.10jqka.com.cn/"}, timeout=8)
            raw = r.text
            if 'data' not in raw.lower():
                continue
            import re
            m = re.search(r'"(\d{8}[^"]+)"', raw)
            if not m:
                continue
            parts = m.group(1).split(";")
            dm: dict[str, float] = {}
            for entry in parts[-40:]:  # 近40天
                fs = entry.split(",")
                if len(fs) >= 2:
                    d_str = fs[0]
                    try:
                        close = float(fs[1])
                        if len(d_str) == 8:
                            d_fmt = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:]}"
                            dm[d_fmt] = close
                            dates_set.add(d_fmt)
                    except Exception:
                        pass
            price_map[code] = dm
        except Exception:
            pass

    if not dates_set:
        return {"dates": [], "values": [], "cost_line": 0}

    sorted_dates = sorted(dates_set)[-30:]
    total_cost = sum(p["avg_cost"] * p["total_shares"] for p in positions)
    values = []
    for date in sorted_dates:
        mv = 0.0
        for pos in positions:
            p_map = price_map.get(pos["code"], {})
            # 找最近有价格的日期
            price = p_map.get(date)
            if price is None:
                # 找最近的
                prev = [v for d, v in sorted(p_map.items()) if d <= date]
                price = prev[-1] if prev else pos["avg_cost"]
            mv += price * pos["total_shares"]
        values.append(round(mv, 2))

    return {"dates": sorted_dates, "values": values, "cost_line": round(total_cost, 2)}


# ══════════════════════════════════════════════════════
#  涨跌归因（今日各持仓对总盈亏的贡献）
# ══════════════════════════════════════════════════════
@router.get("/stats/attribution")
def pnl_attribution():
    """今日各持仓盈亏贡献（用于饼图）。"""
    from tradenest.db.store import get_db
    db = get_db()
    positions = db.execute(
        "SELECT code, name, total_shares FROM positions WHERE status='holding' AND total_shares>0"
    ).fetchall()
    if not positions:
        return {"items": []}

    codes = [p["code"] for p in positions]
    quotes = _get_quotes(codes)

    items = []
    for p in positions:
        q = quotes.get(p["code"], {})
        price = q.get("price", 0)
        prev  = q.get("prev_close", price)
        pnl   = round((price - prev) * p["total_shares"], 2)
        name  = q.get("name") or p["name"] or p["code"]
        items.append({
            "code":  p["code"],
            "name":  name,
            "pnl":   pnl,
            "pct":   round(q.get("pct", 0), 2),
            "shares": p["total_shares"],
        })

    items.sort(key=lambda x: abs(x["pnl"]), reverse=True)
    return {"items": items}
