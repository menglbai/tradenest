"""
================================================================================
文件：tradenest/llm/router.py
作用：模型路由 - 按任务类型选 Provider + 模型
================================================================================

【核心业务功能】
根据"任务类型"自动选择最合适的 LLM Provider 和模型。

不同任务有不同最优模型：
- 简单查询 → 便宜模型（DeepSeek）
- 分析 / Agent → 中等模型（Claude Sonnet 4.6）
- 复杂综合判断 → 高级模型（Claude Opus 4.7）
- 摘要 → 便宜的（Claude Haiku）

【设计要点】
- TaskType 用 Enum 枚举，避免字符串拼写错
- 路由规则可以从配置读（settings.task_routing），也可以代码内置
- 找不到规则时走默认（settings.default_provider_id + 该 Provider.default_model）

【使用示例】
    from tradenest.llm.router import get_provider_for, TaskType
    
    provider, model = get_provider_for(TaskType.ANALYST)
    resp = await provider.chat([...], model=model)

================================================================================
"""

from __future__ import annotations

from enum import Enum

from tradenest.core.config import settings
from tradenest.llm.base import LLMProvider
from tradenest.llm.registry import get_registry


class TaskType(str, Enum):
    """任务类型枚举。
    
    每个 task 对应一个建议的 (provider_id, model) 组合。
    """
    
    SIMPLE_QUERY = "simple_query"
    """简单问答，如"帮我查个数"——便宜模型够用"""
    
    ANALYST = "analyst"
    """分析师 Agent（基本面 / 技术面 等）——主力模型"""
    
    RESEARCHER = "researcher"
    """看多 / 看空研究员——主力模型"""
    
    SYNTHESIS = "synthesis"
    """综合输出 Agent——可能用更强模型"""
    
    SOCRATIC = "socratic"
    """苏格拉底反问——主力模型"""
    
    SUMMARY = "summary"
    """对话摘要 / 笔记总结——便宜模型"""
    
    COMPLIANCE_CHECK = "compliance_check"
    """合规审查（基于关键词，但也可能用 LLM 二次审查）"""
    
    DEFAULT = "default"
    """默认任务"""


# 内置路由规则。Key 是 TaskType.value，Value 是 (provider_id, model_name)。
# None 表示用 Provider 自带的 default_model。
DEFAULT_ROUTING: dict[str, tuple[str, str | None]] = {
    TaskType.SIMPLE_QUERY.value:    ("gateway", "claude-4.6-sonnet-google"),
    TaskType.ANALYST.value:         ("gateway", "claude-4.6-sonnet-google"),
    TaskType.RESEARCHER.value:      ("gateway", "claude-4.6-sonnet-google"),
    TaskType.SYNTHESIS.value:       ("gateway", "claude-4.6-sonnet-google"),
    TaskType.SOCRATIC.value:        ("gateway", "claude-4.6-sonnet-google"),
    TaskType.SUMMARY.value:         ("gateway", "claude-4.6-sonnet-google"),
    TaskType.COMPLIANCE_CHECK.value:("gateway", "claude-4.6-sonnet-google"),
    TaskType.DEFAULT.value:         ("gateway", "claude-4.6-sonnet-google"),
}


def get_provider_for(
    task: TaskType | str,
    *,
    fallback_to_default: bool = True,
) -> tuple[LLMProvider, str]:
    """按任务类型选 Provider 和模型。
    
    Args:
        task: 任务类型枚举或字符串
        fallback_to_default: 路由不到时是否走默认 Provider
    
    Returns:
        (provider 实例, 模型名)
    
    Raises:
        KeyError: 找不到 Provider 且 fallback=False
    """
    task_key = task.value if isinstance(task, TaskType) else task
    
    # 1. 先查路由表
    routing = DEFAULT_ROUTING.get(task_key)
    
    # 2. 找不到 → fallback
    if routing is None:
        if not fallback_to_default:
            raise KeyError(f"任务类型 '{task_key}' 没有路由规则，且 fallback=False")
        routing = DEFAULT_ROUTING[TaskType.DEFAULT.value]
    
    provider_id, model = routing
    
    registry = get_registry()
    try:
        provider = registry.get(provider_id)
    except KeyError:
        # Provider 也没注册 → 走默认 Provider
        if not fallback_to_default:
            raise
        provider = registry.get_default()
    
    # model 为 None 时用 Provider 自带 default
    final_model = model or provider.default_model
    return provider, final_model
