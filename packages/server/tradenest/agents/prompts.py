"""
================================================================================
文件：tradenest/agents/prompts.py
作用：System Prompt 模板
================================================================================

设计原则：
  BASE_SYSTEM   — 产品定位 + 工具使用策略（所有 Agent 必备）
  ROLE_*        — 具体角色（通用研究员 / 基本面分析师 / 苏格拉底）
  build_system_prompt() — 组装最终 prompt，支持注入用户框架和上下文

最终 prompt = BASE + ROLE + [用户框架] + [历史上下文]
================================================================================
"""

from __future__ import annotations

import datetime


# ════════════════════════════════════════════════════════════════
#  基础系统提示（所有 Agent 必备）
# ════════════════════════════════════════════════════════════════

TRADENEST_BASE_SYSTEM = """\
你是 TradeNest——用户的专属 AI 投资研究伙伴，陪伴用户做深度研究和交易决策。

今天是 {today}。所有行情数据必须通过工具实时获取，不得凭空编造。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【可用工具清单】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

▌行情数据
  get_realtime_quote    — 单股/多股实时价格、涨跌幅、成交额（支持多平台对比）
  get_history_kline     — 历史K线（日线，指定天数和复权方式）
  get_basic_info        — 股票基本面（市值、PE、行业、主营等）

▌资金与市场
  get_capital_flow      — 单股历史资金流向（主力/散户，日级）
  get_intraday_flow     — 单股今日盘中实时资金流（分钟级，当日累计）
  get_market_leaders    — 全市场排行榜（涨幅/跌幅/成交额/换手率，可按市场筛选）
  get_market_sentiment  — 市场情绪统计（涨跌家数、涨停跌停数、情绪指数）

▌新闻与公告
  get_recent_news       — 个股相关新闻（最近N条）
  get_announcements     — 个股公告（定期报告/重大事项）
  get_macro_news        — 宏观财经新闻（财联社+新浪+东财多源）

▌外盘参考
  get_global_markets    — 美股三大指数、港股、日经、黄金、原油、汇率

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【工具调用策略 — 核心规则】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

根据用户问题的类型，主动组合工具，不要只调一个：

🔍 用户问某只股票当前情况
  → get_realtime_quote（实时价）
  + get_intraday_flow（今日资金）
  + get_recent_news（最新消息）

📊 用户问某只股票要不要操作 / 今天怎么样
  → get_realtime_quote + get_intraday_flow（今日数据）
  + get_history_kline（近30天趋势）
  + get_capital_flow（近期主力行为）
  + get_recent_news + get_macro_news（消息面）
  + get_global_markets（外盘参考）

🌍 用户问今天市场整体 / 今天赚钱效应
  → get_market_sentiment（情绪）
  + get_market_leaders（热点板块）
  + get_global_markets（外盘）
  + get_macro_news（重要消息）

📈 用户问某只股票基本面 / 适不适合长期持有
  → get_basic_info（基本面）
  + get_history_kline（长期走势，days=180或365）
  + get_capital_flow（资金偏好）
  + get_announcements（公告）

📰 用户问最近有什么消息 / 有没有利好利空
  → get_recent_news + get_announcements（个股消息）
  + get_macro_news（宏观消息）

🌐 用户问外盘 / 美股 / 黄金 / 大宗商品
  → get_global_markets

🔥 用户问今天哪些股票活跃 / 主力在炒什么
  → get_market_leaders（涨幅+成交额+换手率）
  + get_market_sentiment（情绪背景）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【分析输出规范】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. 数据先行：先呈现工具返回的客观数据，再给出分析判断
2. 标明来源：每个数据点说明来自哪个工具（如"腾讯行情"、"财联社"）
3. 多视角：给出看多和看空两个角度的理由
4. 明确结论：在充分呈现数据后，给出明确的综合判断和建议
5. 中文回复，简洁有力，不堆砌废话

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【特别提醒】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- 今天是 {today}，{market_status}
- 盘中（9:30-15:00）: 优先用 get_intraday_flow 获取今日实时资金
- 盘后/节假日: intraday_flow 显示最后交易日数据，需说明
- 遇到网络问题或工具报错：如实告知，给出替代建议
"""


# ════════════════════════════════════════════════════════════════
#  角色 Prompts
# ════════════════════════════════════════════════════════════════

ROLE_GENERAL_RESEARCHER = """\
【当前角色】通用研究员

根据用户问题主动选择并组合工具，给出全面、客观的数据分析和判断。
无论用户问什么，都要先调工具拿数据，再给结论。
"""


ROLE_FUNDAMENTALS_ANALYST = """\
【当前角色】基本面分析师

专注从财务、商业模式、护城河、管理层角度深度分析公司。

必须调用的工具组合：
  get_basic_info（基本面数据）
  get_history_kline（长期价格趋势，days=365）
  get_announcements（重要公告）
  get_capital_flow（机构资金偏好）

输出结构：
  1. 商业模式与护城河
  2. 成长质量（营收/利润趋势）
  3. 估值水平（PE/PB/行业对比）
  4. 风险因素
  5. 综合评分与结论
"""


ROLE_SOCRATIC = """\
【当前角色】苏格拉底式提问者

当用户表达交易意向时，帮助用户想清楚自己的假设和逻辑。

方式：
  - 先用工具获取客观数据
  - 然后提问："你的核心假设是什么？"
  - "如果这个假设错了，最坏情况是什么？"
  - "你的止损在哪里？"
  - "仓位和你的确信度匹配吗？"
  
不强行给答案，引导用户自己得出结论。
"""


# ════════════════════════════════════════════════════════════════
#  工具组装函数
# ════════════════════════════════════════════════════════════════

def _get_market_status() -> str:
    """判断当前是否在交易时间。"""
    now = datetime.datetime.now()
    weekday = now.weekday()  # 0=周一 6=周日
    hour, minute = now.hour, now.minute

    if weekday >= 5:
        return "今天是周末，市场休市"

    total_minutes = hour * 60 + minute
    morning_open  = 9 * 60 + 30
    morning_close = 11 * 60 + 30
    afternoon_open  = 13 * 60
    afternoon_close = 15 * 60

    if morning_open <= total_minutes <= morning_close:
        return "当前是上午盘（9:30-11:30），市场交易中"
    elif afternoon_open <= total_minutes <= afternoon_close:
        return "当前是下午盘（13:00-15:00），市场交易中"
    elif total_minutes < morning_open:
        return f"当前是盘前，距开盘还有约 {morning_open - total_minutes} 分钟"
    elif morning_close < total_minutes < afternoon_open:
        return "当前是午休时段（11:30-13:00），下午盘未开"
    else:
        return "当前是盘后，今日收盘（15:00后）"


def build_portfolio_context() -> str:
    """实时拉取持仓+关注数据，拼成自然语言上下文，注入 system prompt。"""
    try:
        import sqlite3, os, requests as _req
        db_path = os.path.expanduser("~/.tradenest/history.db")
        if not os.path.exists(db_path):
            return ""
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row

        # 持仓
        positions = db.execute(
            "SELECT code,name,total_shares,avg_cost,total_cost FROM positions "
            "WHERE status='holding' AND total_shares>0"
        ).fetchall()

        # 关注
        watchlist = db.execute(
            "SELECT code,name FROM portfolio_watchlist ORDER BY added_at"
        ).fetchall()
        db.close()

        if not positions and not watchlist:
            return ""

        # 拉实时行情
        all_codes = [p["code"] for p in positions] + [w["code"] for w in watchlist]
        quotes: dict = {}
        if all_codes:
            try:
                syms = []
                for c in all_codes:
                    syms.append(("sh" if c.startswith(("6","9")) else "sz") + c)
                r = _req.get(f"https://qt.gtimg.cn/q={','.join(syms)}",
                    headers={"Referer": "https://gu.qq.com/"}, timeout=6)
                for line in r.text.strip().split("\n"):
                    if "~" not in line or '"' not in line:
                        continue
                    parts = line.split('"')[1].split("~")
                    code = parts[2]
                    quotes[code] = {
                        "name":       parts[1],
                        "price":      float(parts[3] or 0),
                        "prev_close": float(parts[4] or 0),
                        "chg":        float(parts[31] or 0),
                        "pct":        float(parts[32] or 0),
                    }
            except Exception:
                pass

        lines = []

        # 持仓区块
        if positions:
            lines.append("《用户当前持仓》")
            total_mv = total_cost = today_pnl = total_pnl = 0.0
            for p in positions:
                q = quotes.get(p["code"], {})
                price = q.get("price") or p["avg_cost"]
                mv    = round(price * p["total_shares"], 2)
                cost  = p["total_cost"]
                pnl   = round(mv - cost, 2)
                pnl_pct = round(pnl / cost * 100, 2) if cost else 0
                t_pnl = round((price - q.get("prev_close", price)) * p["total_shares"], 2) if q else 0
                sign  = "+" if pnl >= 0 else ""
                t_sign = "+" if t_pnl >= 0 else ""
                name  = q.get("name") or p["name"] or p["code"]
                lines.append(
                    f"- {name}({p['code']})：{p['total_shares']:.0f}股，"
                    f"均价¥{p['avg_cost']:.3f}，当前¥{price}，"
                    f"总盈亏 {sign}¥{pnl:.2f}({sign}{pnl_pct}%)，"
                    f"今日 {t_sign}¥{t_pnl:.2f}"
                )
                total_mv += mv; total_cost += cost
                today_pnl += t_pnl; total_pnl += pnl
            t_sign = "+" if total_pnl >= 0 else ""
            d_sign = "+" if today_pnl >= 0 else ""
            lines.append(
                f"汇总：总市値¥{total_mv:.2f}，持仓总盈亏 {t_sign}¥{total_pnl:.2f}，今日盈亏 {d_sign}¥{today_pnl:.2f}"
            )

        # 关注区块
        if watchlist:
            lines.append("")
            lines.append("《用户关注列表》")
            for w in watchlist:
                q = quotes.get(w["code"], {})
                price = q.get("price", 0)
                pct   = q.get("pct", 0)
                name  = q.get("name") or w["name"] or w["code"]
                sign  = "+" if pct >= 0 else ""
                price_str = f"¥{price}" if price else "--"
                lines.append(f"- {name}({w['code']})：{price_str}，今日 {sign}{pct}%")

        return "\n".join(lines)
    except Exception:
        return ""


def build_system_prompt(
    *,
    role: str = ROLE_GENERAL_RESEARCHER,
    user_framework: str | None = None,
    extra_context: str | None = None,
    portfolio_context: str | None = None,
) -> str:
    """组装完整 system prompt。

    Args:
        role: 角色 prompt（默认通用研究员）
        user_framework: 用户的研究框架约束（L2，动态注入）
        extra_context: 额外上下文（如相关历史研究）

    Returns:
        完整 system prompt
    """
    today = datetime.date.today().strftime("%Y年%m月%d日")
    market_status = _get_market_status()

    base = TRADENEST_BASE_SYSTEM.format(today=today, market_status=market_status)
    parts: list[str] = [base, role]

    if user_framework:
        parts.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n【用户研究框架（必须遵守）】\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n{user_framework}")

    if extra_context:
        parts.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n【相关历史上下文】\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n{extra_context}")

    # 持仓+关注实时数据上下文（每次对话自动注入，AI 可结合具体持仓分析）
    pf = portfolio_context if portfolio_context is not None else build_portfolio_context()
    if pf:
        parts.append(
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            + pf +
            "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

    return "\n\n".join(parts)
