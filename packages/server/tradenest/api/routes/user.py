"""
================================================================================
文件：tradenest/api/routes/user.py
作用：用户设置接口——自选股管理 + 价格预警 + 角色偏好
================================================================================

接口列表：
  GET  /api/user/watchlist              获取自选股列表
  POST /api/user/watchlist              添加自选股
  DELETE /api/user/watchlist/{code}     删除自选股
  GET  /api/user/watchlist/quotes       批量获取自选股实时行情

  GET  /api/user/alerts                 获取价格预警列表
  POST /api/user/alerts                 创建价格预警
  DELETE /api/user/alerts/{alert_id}    删除预警
  GET  /api/user/alerts/check           检查是否有触发的预警（前端轮询用）

  GET  /api/user/role                   获取当前角色设置
  POST /api/user/role                   切换角色

数据持久化：SQLite（同 sessions/messages 共用一个 DB）
================================================================================
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from tradenest.db.store import get_db

router = APIRouter(prefix="/api/user", tags=["user"])


# ════════════════════════════════════════════════════════════════
#  DB 初始化（建表）
# ════════════════════════════════════════════════════════════════

def init_user_tables() -> None:
    """创建自选股、预警、角色偏好表（幂等）。"""
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            code       TEXT PRIMARY KEY,
            name       TEXT DEFAULT '',
            added_at   INTEGER NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS price_alerts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code        TEXT    NOT NULL,
            name        TEXT    DEFAULT '',
            direction   TEXT    NOT NULL,   -- 'above' | 'below'
            target      REAL    NOT NULL,
            note        TEXT    DEFAULT '',
            created_at  INTEGER NOT NULL,
            triggered   INTEGER DEFAULT 0,  -- 0=未触发 1=已触发
            triggered_at INTEGER
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    db.commit()


# ════════════════════════════════════════════════════════════════
#  自选股
# ════════════════════════════════════════════════════════════════

class AddWatchRequest(BaseModel):
    code: str
    name: str = ""


@router.get("/watchlist")
def get_watchlist():
    db = get_db()
    rows = db.execute(
        "SELECT code, name, added_at FROM watchlist ORDER BY added_at"
    ).fetchall()
    return {"watchlist": [{"code": r[0], "name": r[1], "added_at": r[2]} for r in rows]}


@router.post("/watchlist")
def add_watchlist(req: AddWatchRequest):
    code = req.code.strip()
    if not code or not code.isdigit() or len(code) != 6:
        raise HTTPException(status_code=400, detail="股票代码必须是6位数字")
    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO watchlist(code, name, added_at) VALUES(?,?,?)",
        (code, req.name, int(time.time())),
    )
    db.commit()
    return {"ok": True, "code": code}


@router.delete("/watchlist/{code}")
def remove_watchlist(code: str):
    db = get_db()
    db.execute("DELETE FROM watchlist WHERE code=?", (code,))
    db.commit()
    return {"ok": True}


@router.get("/watchlist/quotes")
async def watchlist_quotes():
    """批量获取自选股实时行情（供前端刷新用）。"""
    db = get_db()
    rows = db.execute("SELECT code, name FROM watchlist ORDER BY added_at").fetchall()
    if not rows:
        return {"quotes": []}

    codes = [r[0] for r in rows]
    names = {r[0]: r[1] for r in rows}

    # 调实时行情工具
    import tradenest.tools  # noqa: F401 触发注册
    from tradenest.tools import execute_tool
    result = await execute_tool("get_realtime_quote", {"codes": codes})

    # 解析结果（工具返回文本，做简单解析）
    quotes_raw = result.content if not result.is_error else ""
    return {
        "quotes": _parse_quotes(quotes_raw, codes, names),
        "raw": quotes_raw,
    }


def _parse_quotes(text: str, codes: list[str], names: dict[str, str]) -> list[dict]:
    """从工具返回文本中解析行情（简单按行解析）。"""
    result = []
    for code in codes:
        # 在文本里找这个 code 对应的行
        for line in text.split("\n"):
            if code in line:
                result.append({"code": code, "name": names.get(code, ""), "line": line.strip()})
                break
        else:
            result.append({"code": code, "name": names.get(code, ""), "line": "暂无数据"})
    return result


# ════════════════════════════════════════════════════════════════
#  价格预警
# ════════════════════════════════════════════════════════════════

class CreateAlertRequest(BaseModel):
    code: str
    name: str = ""
    direction: str   # 'above' | 'below'
    target: float
    note: str = ""


@router.get("/alerts")
def get_alerts():
    db = get_db()
    rows = db.execute(
        "SELECT id,code,name,direction,target,note,created_at,triggered,triggered_at "
        "FROM price_alerts ORDER BY created_at DESC"
    ).fetchall()
    return {
        "alerts": [
            {
                "id": r[0], "code": r[1], "name": r[2],
                "direction": r[3], "target": r[4], "note": r[5],
                "created_at": r[6], "triggered": bool(r[7]), "triggered_at": r[8],
            }
            for r in rows
        ]
    }


@router.post("/alerts")
def create_alert(req: CreateAlertRequest):
    if req.direction not in ("above", "below"):
        raise HTTPException(status_code=400, detail="direction 必须是 above 或 below")
    if req.target <= 0:
        raise HTTPException(status_code=400, detail="目标价必须大于 0")

    db = get_db()
    cur = db.execute(
        "INSERT INTO price_alerts(code,name,direction,target,note,created_at) VALUES(?,?,?,?,?,?)",
        (req.code.strip(), req.name, req.direction, req.target, req.note, int(time.time())),
    )
    db.commit()
    return {"ok": True, "alert_id": cur.lastrowid}


@router.delete("/alerts/{alert_id}")
def delete_alert(alert_id: int):
    db = get_db()
    db.execute("DELETE FROM price_alerts WHERE id=?", (alert_id,))
    db.commit()
    return {"ok": True}


@router.get("/alerts/check")
async def check_alerts():
    """
    检查未触发预警是否已达到条件。
    前端每 60 秒轮询一次，有触发则返回通知列表。
    """
    db = get_db()
    pending = db.execute(
        "SELECT id,code,name,direction,target,note FROM price_alerts WHERE triggered=0"
    ).fetchall()

    if not pending:
        return {"triggered": []}

    # 批量拉行情
    codes = list({r[1] for r in pending})
    import tradenest.tools  # noqa: F401
    from tradenest.tools import execute_tool
    result = await execute_tool("get_realtime_quote", {"codes": codes})

    # 解析当前价格
    current_prices = _extract_prices(result.content, codes)
    triggered = []

    for row in pending:
        alert_id, code, name, direction, target, note = row
        price = current_prices.get(code)
        if price is None:
            continue

        hit = (direction == "above" and price >= target) or \
              (direction == "below" and price <= target)

        if hit:
            now = int(time.time())
            db.execute(
                "UPDATE price_alerts SET triggered=1, triggered_at=? WHERE id=?",
                (now, alert_id),
            )
            dir_text = "突破" if direction == "above" else "跌破"
            triggered.append({
                "alert_id": alert_id,
                "code": code,
                "name": name or code,
                "direction": direction,
                "target": target,
                "current_price": price,
                "note": note,
                "message": f"⚠️ {name or code}({code}) {dir_text} ¥{target}，当前 ¥{price}",
            })

    db.commit()
    return {"triggered": triggered}


def _extract_prices(text: str, codes: list[str]) -> dict[str, float]:
    """从行情文本里提取各 code 的当前价格（简单正则）。"""
    import re
    prices: dict[str, float] = {}
    for code in codes:
        # 找 ¥数字 在含 code 的行里
        for line in text.split("\n"):
            if code in line:
                m = re.search(r"¥([\d.]+)", line)
                if m:
                    try:
                        prices[code] = float(m.group(1))
                    except ValueError:
                        pass
                break
    return prices


# ════════════════════════════════════════════════════════════════
#  角色设置
# ════════════════════════════════════════════════════════════════

VALID_ROLES = {
    "general":       "通用研究员",
    "fundamentals":  "基本面分析师",
    "socratic":      "苏格拉底模式",
}


class SetRoleRequest(BaseModel):
    role: str


@router.get("/role")
def get_role():
    db = get_db()
    row = db.execute("SELECT value FROM user_settings WHERE key='role'").fetchone()
    role = row[0] if row else "general"
    return {"role": role, "role_name": VALID_ROLES.get(role, "通用研究员")}


@router.post("/role")
def set_role(req: SetRoleRequest):
    if req.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"无效角色，可选: {list(VALID_ROLES.keys())}")
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO user_settings(key,value) VALUES('role',?)",
        (req.role,),
    )
    db.commit()
    return {"ok": True, "role": req.role, "role_name": VALID_ROLES[req.role]}
