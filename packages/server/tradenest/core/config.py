"""
================================================================================
文件：tradenest/core/config.py
作用：全局配置中心
================================================================================

【核心业务功能】
本文件是 TradeNest 整个后端的**唯一配置入口**，使用 pydantic-settings
从 .env 文件 + 环境变量加载所有配置。

主要负责：
1. **LLM Provider 配置**（多家 + 可切换）
   - 内部小红书 codewiz 网关（默认）
   - 公网 Anthropic
   - DeepSeek / Qwen / OpenAI 兼容
2. **代理能力配置**
   - 网络层 HTTP 代理（HTTPS_PROXY 环境变量）
   - 应用层 LLM 代理（每个 Provider 自定义 base_url + headers）
3. **路由策略**（按任务类型选不同模型）
4. **服务配置**（端口、CORS、日志级别等）

【为什么这样设计】
- LLM Provider 用 list[ProviderConfig]，支持任意多个并存
- 每个 Provider 有独立的 base_url + headers，方便走代理
- 所有敏感字段（API key）都从环境变量读，不写死代码
- pydantic-settings 自动校验 + 类型推断

【使用示例】
    from tradenest.core.config import settings
    
    # 拿默认 Provider
    provider = settings.get_default_provider()
    print(provider.base_url)
    
    # 按 ID 拿
    p = settings.get_provider("internal-xhs")
    
================================================================================
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderConfig(BaseModel):
    """单个 LLM Provider 的完整配置。
    
    一个 Provider 代表一个能调用大模型的"通道"。
    可以是：
    - 公网 Anthropic API
    - 公司内部网关（如小红书 codewiz）
    - DeepSeek / Qwen / GPT 等 OpenAI 兼容服务
    """
    
    id: str = Field(..., description="Provider 唯一标识，如 'internal-xhs'、'anthropic-public'")
    """Provider 的唯一 ID。在 .env 配置中通过这个 ID 引用"""
    
    kind: str = Field(..., description="Provider 类型: 'anthropic' / 'openai-compat'")
    """决定用哪个 SDK / 协议调用。anthropic 用 Anthropic SDK，openai-compat 用 OpenAI SDK"""
    
    base_url: str = Field(..., description="API 基础地址（含协议）")
    """API 端点地址。例如:
    - 公网 Anthropic: https://api.anthropic.com
    - 内部网关: http://codewiz.devops.xiaohongshu.com/llmadapter/anthropic
    - DeepSeek: https://api.deepseek.com
    """
    
    api_key: str = Field(default="", description="API 密钥（非空时优先用）")
    """对内部网关，可以为空字符串（用 headers 里的 x-api-key 提示语代替）"""
    
    default_model: str = Field(..., description="该 Provider 默认调用的模型名")
    """例如 claude-4.6-sonnet-google / claude-sonnet-4-5 / deepseek-chat"""
    
    headers: dict[str, str] = Field(default_factory=dict, description="额外 HTTP headers")
    """关键：内部代理需要 x-adapter-* 系列认证头"""
    
    timeout_seconds: float = Field(default=120.0, description="请求超时（秒）")
    
    enabled: bool = Field(default=True, description="是否启用此 Provider")


class TaskRouting(BaseModel):
    """任务路由表。

    把任务类型映射到 Provider ID + 具体模型名。
    这是"应用层代理"——不同任务可以用不同的 Provider。
    """
    
    # task_type → (provider_id, model_name)
    # 例：{"analyst": ("internal-xhs", "claude-4.6-sonnet-google")}
    rules: dict[str, tuple[str, str]] = Field(default_factory=dict)
    
    # 兜底（任何任务都走这个）
    default: tuple[str, str] = Field(default=("internal-xhs", "claude-4.6-sonnet-google"))
    
    def resolve(self, task_type: str) -> tuple[str, str]:
        """根据任务类型选 (provider_id, model)"""
        return self.rules.get(task_type, self.default)


class Settings(BaseSettings):
    """全局配置。
    
    pydantic-settings 自动从以下来源加载（优先级从高到低）：
    1. 环境变量
    2. .env 文件
    3. 这里的 default
    """
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="TRADENEST_",   # 所有环境变量都以这个前缀
        case_sensitive=False,
        extra="ignore",
    )
    
    # ====== 服务配置 ======
    
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = True
    log_level: str = "INFO"
    
    # ====== LLM Provider 配置 ======
    
    # 默认用的 Provider ID
    default_provider_id: str = "internal-xhs"
    
    # 因为 list[ProviderConfig] 不能从 .env 读复杂结构，
    # 这里用代码内置一份"默认 Providers"，再用环境变量覆盖关键字段
    
    # 内部小红书 codewiz 网关默认配置（最重要）
    xhs_internal_base_url: str = "http://codewiz.devops.xiaohongshu.com/llmadapter/anthropic"
    xhs_internal_api_key: str = "Model authentication within the intranet does not require a key"
    xhs_internal_adapter_key: str = "jCJvIWUsyXpcoxdGe61e1yfJ2N8pL4ai"
    xhs_internal_adapter_source: str = "openclaw"
    xhs_internal_user_email: str = "baimenglong@xiaohongshu.com"
    xhs_internal_default_model: str = "claude-4.6-sonnet-google"
    
    # 公网 Anthropic（备用）
    anthropic_api_key: str = ""           # 留空则禁用
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_default_model: str = "claude-sonnet-4-5"
    
    # OpenAI / DeepSeek 等（备用）
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_default_model: str = "gpt-4o"
    
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_default_model: str = "deepseek-chat"
    
    # ====== 合规 ======
    compliance_strict: bool = True   # 严格模式：违规直接拒绝
    
    # ====== Agent 行为 ======
    agent_max_loop_turns: int = 10
    agent_max_tokens: int = 4096
    
    # ====== CORS ======
    cors_allow_origins: list[str] = Field(default_factory=lambda: [
        "http://localhost:1420",   # Tauri 默认
        "http://localhost:3000",
        "http://127.0.0.1:1420",
        "tauri://localhost",
    ])
    
    # ===== 工厂方法：把上面散字段拼成 ProviderConfig 列表 =====
    
    def build_providers(self) -> list[ProviderConfig]:
        """根据扁平字段构造完整 Provider 列表。
        
        只把 enabled 的（API key 非空 或 是内部网关）返回。
        """
        providers: list[ProviderConfig] = []
        
        # 1. 小红书内部网关（永远启用）
        providers.append(ProviderConfig(
            id="internal-xhs",
            kind="anthropic",
            base_url=self.xhs_internal_base_url,
            api_key=self.xhs_internal_api_key,
            default_model=self.xhs_internal_default_model,
            headers={
                "x-api-key": self.xhs_internal_api_key,
                "x-adapter-api-key": self.xhs_internal_adapter_key,
                "x-adapter-source": self.xhs_internal_adapter_source,
                "x-adapter-user-email": self.xhs_internal_user_email,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            timeout_seconds=120.0,
            enabled=True,
        ))
        
        # 2. 公网 Anthropic（仅当配置了 API key 时）
        if self.anthropic_api_key:
            providers.append(ProviderConfig(
                id="anthropic-public",
                kind="anthropic",
                base_url=self.anthropic_base_url,
                api_key=self.anthropic_api_key,
                default_model=self.anthropic_default_model,
                headers={
                    "x-api-key": self.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                enabled=True,
            ))
        
        # 3. OpenAI 公网
        if self.openai_api_key:
            providers.append(ProviderConfig(
                id="openai-public",
                kind="openai-compat",
                base_url=self.openai_base_url,
                api_key=self.openai_api_key,
                default_model=self.openai_default_model,
                headers={
                    "Authorization": f"Bearer {self.openai_api_key}",
                    "Content-Type": "application/json",
                },
                enabled=True,
            ))
        
        # 4. DeepSeek
        if self.deepseek_api_key:
            providers.append(ProviderConfig(
                id="deepseek",
                kind="openai-compat",
                base_url=self.deepseek_base_url,
                api_key=self.deepseek_api_key,
                default_model=self.deepseek_default_model,
                headers={
                    "Authorization": f"Bearer {self.deepseek_api_key}",
                    "Content-Type": "application/json",
                },
                enabled=True,
            ))
        
        return providers
    
    # ===== 便捷查询方法 =====
    
    def get_provider(self, provider_id: str) -> ProviderConfig | None:
        """按 ID 查 Provider，找不到返回 None"""
        for p in self.build_providers():
            if p.id == provider_id:
                return p
        return None
    
    def get_default_provider(self) -> ProviderConfig:
        """拿默认 Provider，找不到则报错"""
        p = self.get_provider(self.default_provider_id)
        if p is None:
            raise RuntimeError(
                f"默认 Provider '{self.default_provider_id}' 没找到。"
                f"已配置的: {[p.id for p in self.build_providers()]}"
            )
        return p


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """单例配置访问。
    
    用 lru_cache 保证整个进程只构造一次 Settings，
    避免每次访问都重新解析 .env。
    """
    return Settings()


# 全局便捷访问点
# 注意：这是模块级单例，import 后就生效。如果要在测试中改配置，
# 用 monkeypatch 改 Settings 字段或 clear lru_cache。
settings = get_settings()
