"""
================================================================================
文件：tradenest/llm/registry.py
作用：LLM Provider 注册中心（单例）
================================================================================

【核心业务功能】
启动时根据 settings.build_providers() 构造所有 Provider 实例，注册到全局表。

之后业务代码通过 ID 拿 Provider：
    from tradenest.llm.registry import get_registry
    
    registry = get_registry()
    provider = registry.get("internal-xhs")
    resp = await provider.chat([...])

【为什么要 Registry】
- Provider 持有 httpx 长连接，不能每次都 new
- 用单例 + 启动时初始化，让所有业务共享同一份 Provider 实例
- shutdown 时一次性 close 所有连接

================================================================================
"""

from __future__ import annotations

from functools import lru_cache

from tradenest.core.config import ProviderConfig, settings
from tradenest.core.logging import get_logger
from tradenest.llm.anthropic_provider import AnthropicProvider
from tradenest.llm.base import LLMProvider
from tradenest.llm.openai_compat_provider import OpenAICompatProvider

log = get_logger("tradenest.llm.registry")


class ProviderRegistry:
    """LLM Provider 注册表。线程安全（实际是单 process 单实例）。"""
    
    def __init__(self) -> None:
        self._providers: dict[str, LLMProvider] = {}
        self._initialized = False
    
    def initialize(self) -> None:
        """初始化所有 Provider 实例。
        
        从 settings.build_providers() 拉所有配置，对应实例化。
        重复调用会跳过（保证幂等）。
        """
        if self._initialized:
            return
        
        for cfg in settings.build_providers():
            if not cfg.enabled:
                continue
            try:
                provider = self._create_provider(cfg)
                self._providers[cfg.id] = provider
                log.info("provider_registered", id=cfg.id, kind=cfg.kind, base_url=cfg.base_url)
            except Exception as e:
                log.error("provider_register_failed", id=cfg.id, error=str(e))
        
        self._initialized = True
        log.info("registry_initialized", count=len(self._providers), ids=list(self._providers.keys()))
    
    @staticmethod
    def _create_provider(cfg: ProviderConfig) -> LLMProvider:
        """根据 kind 选 Provider 实现。"""
        if cfg.kind == "anthropic":
            return AnthropicProvider(cfg)
        if cfg.kind == "openai-compat":
            return OpenAICompatProvider(cfg)
        raise ValueError(f"未知的 Provider kind: {cfg.kind}")
    
    def get(self, provider_id: str) -> LLMProvider:
        """按 ID 获取 Provider，找不到抛 KeyError。"""
        if not self._initialized:
            self.initialize()
        if provider_id not in self._providers:
            raise KeyError(
                f"Provider '{provider_id}' 未注册。"
                f"已注册：{list(self._providers.keys())}"
            )
        return self._providers[provider_id]
    
    def get_default(self) -> LLMProvider:
        """获取默认 Provider（settings.default_provider_id）。"""
        return self.get(settings.default_provider_id)
    
    def list_providers(self) -> list[str]:
        """列出所有已注册 Provider ID。"""
        if not self._initialized:
            self.initialize()
        return list(self._providers.keys())
    
    async def aclose(self) -> None:
        """关闭所有 Provider（清理 httpx client 等资源）。
        
        在 FastAPI lifespan shutdown 时调用。
        """
        for pid, provider in self._providers.items():
            try:
                aclose = getattr(provider, "aclose", None)
                if aclose is not None:
                    await aclose()
                log.info("provider_closed", id=pid)
            except Exception as e:
                log.warning("provider_close_error", id=pid, error=str(e))
        self._providers.clear()
        self._initialized = False


@lru_cache(maxsize=1)
def get_registry() -> ProviderRegistry:
    """全局 Registry 单例。"""
    registry = ProviderRegistry()
    registry.initialize()
    return registry
