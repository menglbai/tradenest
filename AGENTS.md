# AGENTS.md

> Entry point for AI coding assistants (Claude Code / Cursor / Aider / Codex).
> 给 AI Coding 助手的入口文件。

## Project: TradeNest

A long-term AI investment research companion for self-directed investors.

- **Not** an investment advisor product
- Core: 5-layer persistent memory + multi-agent analysis + Socratic questioning
- Stack: Python 3.12 + FastAPI + Anthropic SDK + LangGraph + PG + Tauri + React

## Required Reading Before Coding

Read in this order:

1. [README.md](./README.md) - Project overview (5 min)
2. [docs/0000-阅读指南.md](./docs/0000-阅读指南.md) - Documentation navigation (5 min)
3. [docs/0010-AI接手指南.md](./docs/0010-AI接手指南.md) - **Detailed AI assistant guide** (10 min)
5. [docs/0001-产品需求PRD.md](./docs/0001-产品需求PRD.md) - Full PRD (30 min)

## Quick Rules

- All docs are in Chinese (中文为主, 技术术语英文)
- **Default branch is `master`**, not `main`. Always `git push origin master` / `git pull origin master`.
- Code style: Ruff + mypy strict (Python), Biome + tsc (TypeScript)
- Commit format: Conventional Commits (`feat(scope): subject`)
- Test required for: tools, agents, compliance code
- **Never** clone TradingAgents-CN repo (we reference its design only, write our own code)

## Detailed Guide

For detailed instructions, see [docs/0010-AI接手指南.md](./docs/0010-AI接手指南.md).

This file is intentionally short — its only job is to redirect AI tools to the detailed Chinese guide.
