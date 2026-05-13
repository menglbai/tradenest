"""
================================================================================
文件：tradenest/api/routes/system.py
作用：系统级路由（健康检查 / 配置查看 / Provider 列表）
================================================================================
"""

from __future__ import annotations

from fastapi import APIRouter

from tradenest import __version__
from tradenest.core.config import settings
from tradenest.llm.registry import get_registry
from tradenest.tools import get_all_tools

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/info")
async def info() -> dict:
    """系统信息"""
    return {
        "name": "TradeNest",
        "version": __version__,
        "tagline": "Where your investment ideas come home to grow.",
        "default_provider": settings.default_provider_id,
        "agent_max_rounds": settings.agent_max_loop_turns,
        "compliance_strict": settings.compliance_strict,
    }


@router.get("/providers")
async def list_providers() -> dict:
    """已注册的 LLM Provider 列表"""
    registry = get_registry()
    providers_info = []
    for pid in registry.list_providers():
        p = registry.get(pid)
        providers_info.append({
            "id": p.provider_id,
            "default_model": p.default_model,
            "is_default": pid == settings.default_provider_id,
        })
    return {"providers": providers_info}


@router.get("/tools")
async def list_tools() -> dict:
    """已注册的工具列表"""
    tools = get_all_tools()
    return {
        "tools": [
            {
                "name": t.definition.name,
                "description": t.definition.description,
            }
            for t in tools
        ]
    }


@router.get("/providers/{provider_id}/health")
async def provider_health(provider_id: str) -> dict:
    """测试指定 Provider 是否可用"""
    registry = get_registry()
    try:
        provider = registry.get(provider_id)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    
    ok = await provider.health_check()
    return {"ok": ok, "provider_id": provider_id}
