#!/usr/bin/env bash
# TradeNest 一键安装脚本
set -e

echo "🪺  TradeNest 开发环境安装"
echo "========================="

# 1. 检查必要工具
command -v uv >/dev/null 2>&1 || {
    echo "❌ 需要安装 uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
}
command -v pnpm >/dev/null 2>&1 || {
    echo "❌ 需要安装 pnpm: npm i -g pnpm"
    exit 1
}
command -v docker >/dev/null 2>&1 || {
    echo "⚠️  Docker 未装，跳过容器步骤"
}

# 2. Python 后端
echo "📦 安装 Python 后端依赖..."
cd packages/server
uv sync --all-extras
cd ../..

# 3. 前端 / 桌面（可选）
if [ -d "apps/desktop" ] && [ -f "apps/desktop/package.json" ]; then
    echo "📦 安装桌面客户端依赖..."
    cd apps/desktop
    pnpm install
    cd ../..
fi

# 4. .env
if [ ! -f .env ]; then
    cp .env.example .env
    echo "⚠️  已创建 .env，请填入 ANTHROPIC_API_KEY"
fi

echo ""
echo "✅ 安装完成"
echo ""
echo "下一步:"
echo "  1. 编辑 .env，填入 LLM API key"
echo "  2. docker compose -f docker/docker-compose.dev.yml up -d"
echo "  3. cd packages/server && uv run python -m tradenest.mvp"
