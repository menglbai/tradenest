"""
================================================================================
文件：tradenest/compliance/checker.py
作用：输出合规审查（关键词黑名单）
================================================================================

【核心业务功能】
TradeNest 三道合规防线的**第二道**——输出后置审查。

每条 LLM 输出在送给用户前，先经过这里检查：
1. **禁用模式**（FORBIDDEN_PATTERNS）：检测到直接拦截
2. **警告模式**（WARNING_PATTERNS）：记录但不拦截

详见 docs/0002-合规边界.md §4.2 输出审查机制。

【为什么不只靠 system prompt】
- LLM 偶尔会"越界"（特别是模型升级后）
- 用户可能用提示词注入诱导违规输出
- 审查机制是兜底——多一层保险

【用法】
    from tradenest.compliance import check_compliance, add_disclaimer
    
    text = await llm_response()
    check = check_compliance(text)
    if not check.passed:
        # 拦截或重新生成
        text = "[已拦截违规输出]"
    
    final_text = add_disclaimer(text)

================================================================================
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tradenest.core.logging import get_logger

log = get_logger("tradenest.compliance")


# ============================================================
# 黑名单：违规模式（中文 + 英文）
# ============================================================

FORBIDDEN_PATTERNS: list[tuple[str, str]] = [
    # (regex, 描述)
    
    # 直接建议
    (r"建议(您|你)?\s*(买入|卖出|加仓|减仓|抄底|逃顶|清仓)", "直接给买卖建议"),
    (r"推荐(您|你)?\s*(买入|关注|入手|买进)", "推荐买入"),
    (r"应该(立即|马上)?\s*(买|卖|入手|清仓|加仓|减仓)", "应该买卖"),
    (r"(强烈|大力|果断)?\s*(看多|看空|看好|看涨|看跌)", "明确方向性预测"),
    
    # 预测股价
    (r"目标\s*(价|位)[\s::]*[\d.]+", "目标价"),
    (r"(短期|中期|长期)?(将|会|预计)?\s*(涨|跌)\s*到\s*[\d.]+", "股价预测"),
    (r"(短期|中期|长期)\s*(将|会|可能)\s*(上涨|下跌)", "趋势预测"),
    (r"突破\s*[\d.]+\s*(后|元)?\s*(将|会)", "突破预测"),
    
    # 承诺收益
    (r"(必涨|稳赚|包赚|稳赚不赔|保证收益|包赢)", "收益承诺"),
    (r"成功率\s*\d+\s*%", "成功率承诺"),
    (r"收益率\s*(超过|达到|稳定)\s*\d+", "收益率承诺"),
    
    # 误导
    (r"AI\s*(永远不会错|比人类(更准|更聪明))", "AI 能力夸大"),
    (r"内幕\s*(消息|信息|情报)", "内幕消息"),
]


# 警告（不拦截，但记录）
WARNING_PATTERNS: list[tuple[str, str]] = [
    (r"(机会|时机)", "机会暗示"),
    (r"(低估|高估)", "估值判断"),
]


# ============================================================
# 数据模型
# ============================================================

@dataclass
class ComplianceCheck:
    """合规检查结果"""
    
    passed: bool
    """是否通过（无违规）"""
    
    violations: list[dict[str, str]] = field(default_factory=list)
    """违规清单：[{"pattern": "...", "match": "...", "description": "..."}]"""
    
    warnings: list[dict[str, str]] = field(default_factory=list)
    """警告清单（同上格式）"""


# ============================================================
# 主函数
# ============================================================

def check_compliance(text: str) -> ComplianceCheck:
    """检查文本是否合规。
    
    Args:
        text: AI 输出文本
    
    Returns:
        ComplianceCheck（含违规和警告清单）
    """
    violations: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    
    if not text:
        return ComplianceCheck(passed=True)
    
    for pattern, description in FORBIDDEN_PATTERNS:
        for match in re.finditer(pattern, text):
            violations.append({
                "pattern": pattern,
                "match": match.group(0),
                "description": description,
            })
    
    for pattern, description in WARNING_PATTERNS:
        for match in re.finditer(pattern, text):
            warnings.append({
                "pattern": pattern,
                "match": match.group(0),
                "description": description,
            })
    
    result = ComplianceCheck(
        passed=len(violations) == 0,
        violations=violations,
        warnings=warnings,
    )
    
    if violations:
        log.warning(
            "compliance_violation_detected",
            count=len(violations),
            samples=[v["match"] for v in violations[:3]],
        )
    
    return result


# ============================================================
# 免责声明
# ============================================================

DISCLAIMER = (
    "\n\n---\n"
    "⚠️ 以上仅为研究参考，不构成投资建议。所有数据可能有误差或延迟，"
    "投资决策请自行做出，自负盈亏。"
)


def add_disclaimer(text: str, *, force: bool = False) -> str:
    """在文本末尾追加免责声明。
    
    Args:
        text: AI 输出
        force: 即使已经有免责声明也再追加
    
    Returns:
        带免责声明的文本
    """
    if not text:
        return DISCLAIMER.strip()
    
    # 已经有免责声明就跳过（避免重复）
    if not force and ("不构成投资建议" in text or "投资建议" in text):
        return text
    
    return text + DISCLAIMER


# ============================================================
# 高阶辅助：审查 + 处理
# ============================================================

def safe_output(text: str, *, strict: bool = True) -> tuple[str, ComplianceCheck]:
    """对 LLM 输出做完整审查 + 处理。
    
    Args:
        text: 原始 LLM 输出
        strict: 严格模式 — 违规直接替换为安全模板；非严格 — 仅警告
    
    Returns:
        (最终展示给用户的文本, 审查结果)
    """
    check = check_compliance(text)
    
    if not check.passed and strict:
        # 严格模式：直接拦截
        log.error("compliance_blocked", violations=check.violations)
        safe_text = (
            "（系统检测到本次回复包含合规风险内容，已被拦截。\n"
            "TradeNest 不能给具体投资建议或股价预测。\n"
            "你可以尝试：让我帮你整理某只股票的基本面数据 / 多视角分析 / 跟你历史研究的对比。）"
        )
        return add_disclaimer(safe_text), check
    
    return add_disclaimer(text), check
