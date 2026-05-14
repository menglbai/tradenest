"""
================================================================================
文件：tradenest/core/config.py
作用：全局配置中心
================================================================================

【LLM Provider 配置】
支持多个 Provider 并存，按优先级切换：
  - gateway:  自定义 LLM 网关（通过 TRADENEST_GATEWAY_BASE_URL 配置）
  - anthropic: 公网 Anthropic API
  - openai:    OpenAI 公网
  - deepseek:  DeepSeek API

通过 .env 文件配置，所有字段以 TRADENEST_ 前缀开头。

================================================================================
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderConfig(BaseModel):
    """单个 LLM Provider 的完整配置。"""

    id: str = Field(..., description="Provider 唯一标识，如 'gateway'、'anthropic'")
    kind: str = Field(..., description="协议类型: 'anthropic' / 'openai-compat'")
    base_url: str = Field(..., description="API 基础地址")
    api_key: str = Field(default="", description="API 密钥")
    default_model: str = Field(..., description="默认调用的模型名")
    headers: dict[str, str] = Field(default_factory=dict, description="额外 HTTP headers")
    timeout_seconds: float = Field(default=120.0)
    enabled: bool = Field(default=True)


class TaskRouting(BaseModel):
    """任务路由表：任务类型 → (provider_id, model_name)。"""

    rules: dict[str, tuple[str, str]] = Field(default_factory=dict)
    default: tuple[str, str] = Field(default=("gateway", "claude-4.6-sonnet-google"))

    def resolve(self, task_type: str) -> tuple[str, str]:
        return self.rules.get(task_type, self.default)


class Settings(BaseSettings):
    """全局配置，从 .env 文件 + 环境变量加载。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="TRADENEST_",
        case_sensitive=False,
        extra="ignore",
    )

    # ====== 服务配置 ======
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = True
    log_level: str = "INFO"

    # ====== LLM Provider 配置 ======

    # 默认 Provider ID
    default_provider_id: str = "gateway"

    # Provider 1: 自定义 LLM 网关（通过 base_url 指向任意兼容 Anthropic 协议的网关）
    # 在 .env 中设置 TRADENEST_GATEWAY_BASE_URL 和相关认证头
    gateway_base_url: str = ""                  # 必须在 .env 中配置
    gateway_api_key: str = ""                   # 网关 API key（可选）
    gateway_default_model: str = "claude-4.6-sonnet-google"
    # 额外认证头，JSON 格式字符串，如：{"x-adapter-api-key": "xxx", "x-adapter-source": "tradenest"}
    gateway_extra_headers: str = ""             # 留空则不加额外头
    gateway_user_email: str = ""                # 用于 x-adapter-user-email 等认证头

    # Provider 2: 公网 Anthropic
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_default_model: str = "claude-opus-4-5"

    # Provider 3: OpenAI 公网
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_default_model: str = "gpt-4o"

    # Provider 4: DeepSeek
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_default_model: str = "deepseek-chat"

    # ====== 合规（默认关闭） ======
    compliance_strict: bool = False

    # ====== Agent 行为 ======
    agent_max_loop_turns: int = 10
    agent_max_tokens: int = 4096

    # ====== 数据源 ======
    quote_sources: str = "tencent,sina,ths"

    # ====== CORS ======
    cors_allow_origins: list[str] = Field(default_factory=lambda: [
        "http://localhost:1420",
        "http://localhost:3000",
        "http://127.0.0.1:1420",
        "tauri://localhost",
        "*",
    ])

    # ===== 构造 Provider 列表 =====

    def build_providers(self) -> list[ProviderConfig]:
        """根据配置构造已启用的 Provider 列表。"""
        import json
        providers: list[ProviderConfig] = []

        # 1. 自定义网关（配置了 base_url 才启用）
        if self.gateway_base_url:
            headers: dict[str, str] = {
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }
            if self.gateway_api_key:
                headers["x-api-key"] = self.gateway_api_key
            if self.gateway_user_email:
                headers["x-adapter-user-email"] = self.gateway_user_email
            # 解析额外 headers
            if self.gateway_extra_headers:
                try:
                    extra = json.loads(self.gateway_extra_headers)
                    headers.update(extra)
                except Exception:
                    pass
            providers.append(ProviderConfig(
                id="gateway",
                kind="anthropic",
                base_url=self.gateway_base_url,
                api_key=self.gateway_api_key,
                default_model=self.gateway_default_model,
                headers=headers,
                enabled=True,
            ))

        # 2. 公网 Anthropic
        if self.anthropic_api_key:
            providers.append(ProviderConfig(
                id="anthropic",
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

        # 3. OpenAI
        if self.openai_api_key:
            providers.append(ProviderConfig(
                id="openai",
                kind="openai-compat",
                base_url=self.openai_base_url,
                api_key=self.openai_api_key,
                default_model=self.openai_default_model,
                headers={"Authorization": f"Bearer {self.openai_api_key}"},
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
                headers={"Authorization": f"Bearer {self.deepseek_api_key}"},
                enabled=True,
            ))

        return providers

    def get_provider(self, provider_id: str) -> ProviderConfig | None:
        for p in self.build_providers():
            if p.id == provider_id:
                return p
        return None

    def get_default_provider(self) -> ProviderConfig:
        p = self.get_provider(self.default_provider_id)
        if p is None:
            # 如果默认 Provider 没配置，取第一个可用的
            providers = self.build_providers()
            if providers:
                return providers[0]
            raise RuntimeError(
                f"没有可用的 LLM Provider。"
                f"请在 .env 中配置 TRADENEST_GATEWAY_BASE_URL 或 TRADENEST_ANTHROPIC_API_KEY。"
            )
        return p


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
