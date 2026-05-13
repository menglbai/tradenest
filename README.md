# TradeNest

> 你的私人 AI 投资研究伙伴。陪你 1-3-5 年——记得你的所有研究、判断与演变。
>
> _Where your investment ideas come home to grow._
> _让投资研究有家可归。_

🚧 **Pre-alpha** · 启动日期 2026-05-13

---

## 这是什么？

**TradeNest** 是一个长期陪伴你做投资研究的 AI 伙伴。它不替你决策，
也不给你买卖建议。它做的是：

- 🧠 **记得**你的所有研究、判断、决策（5 层持久记忆）
- 📡 **盯着**市场（行情 / 公告 / 新闻 / 研报 / 资金流）
- 🔬 **多 Agent 深度分析**（5 分析师 + 看多看空辩论 + 三方风控）
- 💬 **苏格拉底式反问**——决策时逼你想清楚（不替你下结论）
- 📊 **跨时间复盘**——周报 / 月报 / 年报，看你判断的演变

## 为什么叫 TradeNest？

> "Nest" = 巢穴、归宿。
>
> 这不是工具，不是助手，不是平台——
> 是你所有投资研究、判断、决策**长期归宿**的地方。
>
> 投资最稀缺的不是信息，是**时间累积**。你 5 年前的判断逻辑，今天还记得吗？
> TradeNest 让你的每一次研究都有家可回。

---

## 📚 项目文档（按编号阅读）

所有项目档案归档在 [docs/](./docs/) 目录。

**强烈建议从** [docs/0000-阅读指南.md](./docs/0000-阅读指南.md) **开始**——它会告诉你按什么顺序读。

### 文档清单

| 编号 | 文档 | 用途 |
|------|------|------|
| 0000 | [阅读指南](./docs/0000-阅读指南.md) | 文档导航（**第一站**） |
| 0001 | [产品需求 PRD](./docs/0001-产品需求PRD.md) | 完整产品需求文档 |
| 0002 | [合规边界](./docs/0002-合规边界.md) | **合规红线（必读）** |
| 0003 | [决策日志 ADR](./docs/0003-决策日志ADR.md) | 18 条关键决策的来龙去脉 |
| 0004 | [参考项目分析](./docs/0004-参考项目分析.md) | TradingAgents-CN 借鉴 vs 差异化 |
| 0005 | [整体架构](./docs/0005-整体架构.md) | 架构图 + 模块划分 + 数据流 |
| 0006 | [技术选型](./docs/0006-技术选型.md) | 24 项技术选型理由 |
| 0007 | [数据模型](./docs/0007-数据模型.md) | 5 层记忆 + 完整 DDL |
| 0008 | [Agent 设计](./docs/0008-Agent设计.md) | Agent 编排 / 工具 / Hook |
| 0009 | [路线图](./docs/0009-路线图.md) | 8 个月详细规划 |
| 0010 | [AI 接手指南](./docs/0010-AI接手指南.md) | 给 AI Coding 助手 |
| 0011 | [协作规范](./docs/0011-协作规范.md) | 代码风格 + PR 流程 |
| 0012 | [变更日志](./docs/0012-变更日志.md) | 版本变更（SemVer） |
| 0013 | [第一周任务](./docs/0013-第一周任务.md) | Day 1 启动清单 |
| 0099 | [讨论时间线](./docs/0099-讨论时间线.md) | 立项讨论历史归档 |

> **AI Coding 助手**（Claude Code / Cursor 等）请先读根目录 [AGENTS.md](./AGENTS.md)。

---

## 技术栈速览

| 层 | 技术 |
|----|------|
| **后端语言** | Python 3.12 |
| **后端框架** | FastAPI |
| **Agent 框架** | Anthropic Claude Agent SDK + LangGraph |
| **LLM 主模型** | Claude Sonnet 4.6 / Opus 4.7 |
| **LLM 备选** | DeepSeek V3.2 / Qwen3 / GPT-5 |
| **关系数据库** | PostgreSQL 16 |
| **向量数据库** | pgvector |
| **缓存 / 队列** | Redis 7 + Celery |
| **桌面客户端** | Tauri 2.x + React 19 + TypeScript |
| **图表** | TradingView Lightweight Charts + ECharts |
| **浏览器扩展** | WXT 框架 (Manifest V3) |
| **数据源** | AkShare + Tushare + Playwright（雪球/微信/知乎） |
| **包管理** | uv (Python) + pnpm (前端) |
| **代码质量** | Ruff + mypy + Biome |

详见 [docs/0006-技术选型.md](./docs/0006-技术选型.md)。

---

## 核心模块

| 模块 | 描述 | 详见 |
|------|------|------|
| **A. 记忆系统** | 5 层持久记忆（用户画像/研究框架/笔记/决策档案/对话历史） | [0007 §2](./docs/0007-数据模型.md#2-五层记忆详细设计) |
| **B. 多 Agent 分析** | 5 分析师 + 多空辩论 + 三方风控辩论 | [0008 §2](./docs/0008-Agent设计.md#2-多-agent-编排) |
| **C. 实时市场** | A 股/港股/美股 + 资金流 + 主动推送 | [0005 §5](./docs/0005-整体架构.md#5-数据流) |
| **D. 苏格拉底对话** | 决策时反问、跨时间反馈 | [0008 §9](./docs/0008-Agent设计.md#9-苏格拉底对话策略) |
| **E. 复盘系统** | 周报/月报/年报 + 判断演变曲线 | [0001 §10](./docs/0001-产品需求PRD.md#10-模块-e跨时间复盘系统) |
| **F. 信息流整合** | 公众号/雪球/知乎/研报抓取 | [0005 §6](./docs/0005-整体架构.md#6-数据采集层) |

---

## 快速开始

```bash
# 1. 装依赖
./scripts/setup.sh

# 2. 起开发环境（PG + Redis）
docker compose -f docker/docker-compose.dev.yml up -d

# 3. 配置 API keys
cp .env.example .env
# 填入 ANTHROPIC_API_KEY 等

# 4. 启动后端
cd packages/server
uv run uvicorn tradenest.main:app --reload

# 5. 启动桌面客户端（另开终端）
cd apps/desktop
pnpm install
pnpm tauri dev
```

详见 [docs/0013-第一周任务.md](./docs/0013-第一周任务.md)。

---

## 项目状态

| Phase | 时间 | 模块 | 状态 |
|-------|------|------|------|
| Phase 0 | Week 1-4 | 基础设施 + Agent 内核 + 单源数据 | 🚧 进行中 |
| Phase 1 | Month 2-3 | 5 层记忆 + 苏格拉底对话 | 📋 |
| Phase 2 | Month 4-5 | 多 Agent 编排 + 全数据源 | 📋 |
| Phase 3 | Month 6-7 | 实时推送 + 复盘系统 | 📋 |
| Phase 4 | Month 8 | 桌面客户端 + 私测发布 | 📋 |

详见 [docs/0009-路线图.md](./docs/0009-路线图.md)。

---

## License

Apache 2.0（详见 [LICENSE](./LICENSE)）

## ⚠️ 免责声明

**TradeNest 仅作为研究辅助工具，不构成任何投资建议**。
- 不推荐具体证券
- 不预测股价
- 不替你下单
- 所有数据仅供参考，可能存在错误或延迟

您应自行做出投资决策并承担相应风险。详见 [docs/0002-合规边界.md](./docs/0002-合规边界.md)。

---

🪺 _Where your investment ideas come home to grow._
