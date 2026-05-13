"""
================================================================================
文件：tradenest/compliance/checker.py
作用：输出审查（直通模式，不拦截任何内容）
================================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tradenest.core.logging import get_logger

log = get_logger("tradenest.compliance")


# ============================================================
# 数据模型
# ============================================================

@dataclass
class ComplianceCheck:
    """审查结果（始终通过）"""

    passed: bool
    violations: list[dict[str, str]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)


# ============================================================
# 主函数（直通）
# ============================================================

def check_compliance(text: str) -> ComplianceCheck:
    """直通审查，始终返回通过。"""
    return ComplianceCheck(passed=True)


# ============================================================
# 免责声明（可选，不强制追加）
# ============================================================

DISCLAIMER = ""


def add_disclaimer(text: str, *, force: bool = False) -> str:
    """直接返回原文，不追加任何声明。"""
    return text


# ============================================================
# 高阶辅助
# ============================================================

def safe_output(text: str, *, strict: bool = True) -> tuple[str, ComplianceCheck]:
    """直通模式，不做任何拦截或修改。"""
    return text, ComplianceCheck(passed=True)
