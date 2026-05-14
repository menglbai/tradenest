"""
Settings API — 在网页上配置 LLM key、网关地址、数据源等
配置持久化到 SQLite，优先级高于 .env 文件
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
import sqlite3, os, json

router = APIRouter(prefix="/api/settings", tags=["settings"])

DB_PATH = os.path.expanduser("~/.tradenest/history.db")


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


# 默认配置项（key → 展示名/类型/说明）
SETTINGS_META = {
    "gateway_base_url":      {"label": "LLM 网关地址",      "type": "text",     "placeholder": "https://api.anthropic.com 或内部网关地址"},
    "gateway_api_key":       {"label": "网关 API Key",      "type": "password", "placeholder": "内部网关用 x-adapter-api-key，公网用 sk-ant-..."},
    "gateway_source":        {"label": "来源标识",           "type": "text",     "placeholder": "tradenest"},
    "anthropic_api_key":     {"label": "Anthropic API Key", "type": "password", "placeholder": "sk-ant-xxxxxx（公网直连时填）"},
    "default_model":         {"label": "默认模型",           "type": "text",     "placeholder": "claude-4.6-sonnet-google"},
    "quote_sources":         {"label": "行情数据源优先级",   "type": "text",     "placeholder": "tencent,sina,ths"},
    "http_proxy":            {"label": "HTTP 代理",         "type": "text",     "placeholder": "http://127.0.0.1:7890（留空=不用）"},
}


class SettingItem(BaseModel):
    key: str
    value: str


class SettingsBatch(BaseModel):
    settings: dict[str, str]


@router.get("")
def get_settings():
    """获取所有配置（返回 meta + 当前值，密码类型脱敏显示）"""
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    db_values = {r[0]: r[1] for r in rows}

    result = {}
    for key, meta in SETTINGS_META.items():
        value = db_values.get(key, "")
        result[key] = {
            **meta,
            "value": "••••••••" if meta["type"] == "password" and value else value,
            "has_value": bool(value),
        }
    return {"settings": result}


@router.put("")
def save_settings(body: SettingsBatch):
    """批量保存配置，立即生效（更新环境变量）"""
    conn = get_db()
    updated = []
    for key, value in body.settings.items():
        if key not in SETTINGS_META:
            continue
        if value == "••••••••":  # 脱敏占位符，跳过（不覆盖原值）
            continue
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (key, value)
        )
        # 同步更新进程环境变量，本次运行立即生效
        _apply_to_env(key, value)
        updated.append(key)
    conn.commit()
    conn.close()
    return {"ok": True, "updated": updated}


@router.get("/effective")
def get_effective_settings():
    """返回当前实际生效的配置（DB 覆盖 .env）"""
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    db_values = {r[0]: r[1] for r in rows}

    from tradenest.core.config import settings as cfg
    return {
        "gateway_base_url": db_values.get("gateway_base_url") or cfg.gateway_base_url,
        "default_model":    db_values.get("default_model")    or cfg.gateway_default_model,
        "quote_sources":    db_values.get("quote_sources")    or cfg.quote_sources,
        "has_gateway_key":  bool(db_values.get("gateway_api_key")),
        "has_anthropic_key":bool(db_values.get("anthropic_api_key")),
        "source": "db+env",
    }


def _apply_to_env(key: str, value: str):
    """把 Settings DB 的值同步到进程环境变量，让当前运行立即生效"""
    mapping = {
        "gateway_base_url":  "TRADENEST_GATEWAY_BASE_URL",
        "gateway_api_key":   "_TRADENEST_GATEWAY_API_KEY",   # 内部用，config 读取
        "anthropic_api_key": "ANTHROPIC_API_KEY",
        "default_model":     "TRADENEST_DEFAULT_MODEL",
        "quote_sources":     "TRADENEST_QUOTE_SOURCES",
        "http_proxy":        "TRADENEST_HTTP_PROXY",
    }
    env_key = mapping.get(key)
    if env_key:
        os.environ[env_key] = value


def load_db_settings_to_env():
    """启动时调用：把 DB 里的配置覆盖到环境变量，优先级高于 .env"""
    try:
        conn = get_db()
        rows = conn.execute("SELECT key, value FROM settings WHERE value != ''").fetchall()
        conn.close()
        for key, value in rows:
            _apply_to_env(key, value)
    except Exception:
        pass  # 首次启动 DB 不存在，静默忽略
