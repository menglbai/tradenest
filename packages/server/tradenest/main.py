"""TradeNest FastAPI 主应用。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tradenest import __version__


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    """启动 / 关闭钩子。"""
    # TODO: 启动时初始化数据库、Redis、Agent runtime
    print(f"🪺  TradeNest v{__version__} starting...")
    yield
    # TODO: 关闭时优雅清理
    print("🪺  TradeNest shutting down...")


app = FastAPI(
    title="TradeNest",
    description="Your private AI investment research companion",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:1420", "tauri://localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "name": "TradeNest",
        "version": __version__,
        "tagline": "Where your where smart investment ideas dock.",
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
