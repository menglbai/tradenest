"""
================================================================================
文件：tradenest/main.py
作用：FastAPI 应用入口
================================================================================

【核心业务功能】
TradeNest 后端 HTTP 服务的总入口。

负责：
1. 创建 FastAPI app 实例
2. 配置 CORS（让桌面客户端 / 浏览器扩展能跨域调用）
3. 注册所有路由（chat / system）
4. 配置启动 / 关闭 lifespan
   - 启动：初始化日志、注册 LLM Provider
   - 关闭：清理 httpx client 等资源

【启动方式】
    cd packages/server
    uv run uvicorn tradenest.main:app --reload --host 0.0.0.0 --port 8000

【访问示例】
    curl http://localhost:8000/             # 根路径
    curl http://localhost:8000/health       # 健康检查
    curl http://localhost:8000/system/info  # 系统信息
    curl -X POST http://localhost:8000/chat \\
         -H "Content-Type: application/json" \\
         -d '{"message":"贵州茅台多少钱？"}'

================================================================================
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from tradenest import __version__
from tradenest.api.routes import chat as chat_routes
from tradenest.api.routes import system as system_routes
from tradenest.api.routes import market as market_routes
from tradenest.api.routes import sessions as sessions_routes
from tradenest.api.routes import tools as tools_routes
from tradenest.api.routes import chart as chart_routes
from tradenest.api.routes import user as user_routes

# 静态文件目录
_STATIC_DIR = Path(__file__).parent.parent / "static"
from tradenest.core.config import settings
from tradenest.core.logging import get_logger, setup_logging
from tradenest.llm.registry import get_registry

# 重要：import tools 模块触发所有 @register_tool 注册
import tradenest.tools  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期。"""
    # ===== 启动 =====
    setup_logging()
    log = get_logger("tradenest.main")
    log.info(
        "tradenest_starting",
        version=__version__,
        host=settings.host,
        port=settings.port,
    )
    
    # 触发 LLM Provider 注册
    registry = get_registry()
    log.info("providers_ready", ids=registry.list_providers())
    
    # 触发工具注册（已由 import 自动完成）
    from tradenest.tools import get_all_tools
    log.info("tools_ready", count=len(get_all_tools()))

    # 初始化数据库表（sessions/messages/watchlist/alerts/settings）
    from tradenest.db.store import init_db
    from tradenest.api.routes.user import init_user_tables
    init_db()
    init_user_tables()
    log.info("db_ready")
    
    yield
    
    # ===== 关闭 =====
    log.info("tradenest_shutting_down")
    await registry.aclose()
    log.info("tradenest_stopped")


# ============================================================
# FastAPI App
# ============================================================

app = FastAPI(
    title="TradeNest",
    description="Your private AI investment research companion",
    version=__version__,
    lifespan=lifespan,
    # 关合规相关：明确 docs 不展示给未授权用户（v1 暂时全开）
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS（让桌面 / 扩展能调）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# 注册路由
# ============================================================

app.include_router(chat_routes.router)
app.include_router(system_routes.router)
app.include_router(market_routes.router)
app.include_router(sessions_routes.router)
app.include_router(tools_routes.router)
app.include_router(chart_routes.router)
app.include_router(user_routes.router)

# 静态文件（网页端）
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ============================================================
# 根路径 / 健康检查
# ============================================================

@app.get("/")
async def root():
    """根路径：如果 static/index.html 存在就返回网页，否则返回 JSON"""
    index = _STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {
        "name": "TradeNest",
        "version": __version__,
        "tagline": "Where your investment ideas come home to grow.",
        "docs": "/docs",
    }


@app.get("/health")
async def health() -> dict[str, str]:
    """K8s 风格健康检查"""
    return {"status": "ok"}
