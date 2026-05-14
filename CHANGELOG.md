# Changelog

所有重要变更都记录在这里。格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

变更类型说明：
- `新增` — 新功能
- `修复` — Bug 修复
- `优化` — 体验/性能改进
- `移除` — 删除功能或代码
- `文档` — 文档变更
- `重构` — 代码结构调整，不影响功能

---

## [Unreleased]

---

## [0.1.0] - 2026-05-14

### 新增

#### 核心后端
- LLM 抽象层：支持 4 类 Provider（内部网关 / 公网 Anthropic / OpenAI 兼容 / DeepSeek）
- Agent loop：流式 + 非流式，支持工具调用循环，最大轮数控制
- FastAPI 后端：`/chat`、`/chat/stream`、`/system/info` 等接口
- SQLite 持久化：对话历史、会话管理、自选股、持仓、预警、Settings 均本地存储

#### 行情数据工具（12 个）
- `get_realtime_quote`：实时行情，腾讯/新浪/同花顺三源 + 自动降级 + 多平台对比模式
- `get_history_kline`：历史 K 线（同花顺，支持日/周/月/季/年）
- `get_basic_info`：个股基本面
- `get_capital_flow`：资金流向
- `get_recent_news`：个股新闻
- `get_announcements`：公告
- `get_intraday_flow`：盘中实时资金流（分钟级）
- `get_global_markets`：全球市场行情（美股/港股/汇率/大宗）
- `get_macro_news`：宏观政策新闻（财联社 + 新浪 + 东财三源）
- `get_market_leaders`：涨跌幅榜/成交额榜/换手率榜
- `get_market_sentiment`：市场情绪（涨跌家数、涨停板等）
- `mock_get_stock_demo`：Mock 演示工具

#### 网页端
- 深色主题，粒子动画背景
- 多会话管理：左侧会话列表，支持新建/切换/删除，首条消息自动命名
- 流式输出：AI 边思考边显示，含打字光标效果
- 行情条：顶部实时滚动（上证/深证/创业板/道指/纳指/恒生/黄金）
- 股票搜索：全局搜索框，支持代码/名称，内联操作菜单（关注/K线/持仓/AI分析）
- 快捷操作：6 个悬浮圆形按钮（持仓诊断/对话摘要/导出长图/今日盈亏/大盘分析/今日新闻）
- 快捷问句：欢迎页 6 个常用问题一键发送
- 对话功能：消息星标收藏、对话长图导出（含水印）
- 字体调节（A-/A+）、深色/浅色主题切换
- 键盘快捷键：`Ctrl+K` 聚焦搜索、`Ctrl+/` 收起侧栏

#### 右侧面板（6 个 Tab）
- **💼 持仓**：新建/加仓/卖出/持仓总览/历史持仓；收益曲线/涨跌归因/月度盈亏/交易日历四个子图表
- **👁 关注**：关注列表，实时行情，30s 自动刷新，内联表单添加
- **预警**：价格预警（高于/低于/涨幅/跌幅 4 种），60s 轮询，触发浏览器通知
- **快讯**：财联社实时电报，点条目让 AI 分析
- **外盘**：美股/港股/日韩/汇率/大宗 6 组分类实时数据
- **K线**：同花顺真实数据，日/日长/周/月切换，前复权/后复权/不复权，MA5/10/20/60，成交量副图，持仓成本线标注
- 右侧面板支持拖拽调宽（280~680px）

#### AI 能力
- System Prompt 场景化策略：自动识别问题类型，主动组合多个工具
- 持仓上下文注入：每次对话自动携带用户持仓/关注/实时行情到 Prompt
- 角色切换：通用研究员 / 基本面分析师 / 苏格拉底模式
- 持仓诊断：一键分析所有持仓，给出建议
- 对话摘要：一键总结本次对话核心结论

#### 配置
- Settings 页面：网关地址/SSO Cookie/API Key/数据源优先级等，存 SQLite，热更新
- `.env` 配置文件：每个参数有详细注释和场景说明
- 多数据源配置：`TRADENEST_QUOTE_SOURCES=tencent,sina,ths` 可配置优先级

#### CLI
- `tradenest info`：系统信息
- `tradenest health`：Provider 健康检查
- `tradenest ask "..."`：单次问答
- `tradenest chat`：多轮对话（支持 `--stream`）

### 修复
- 修复 `macro.py` 三个工具未注册到 Agent 的 bug
- 修复 `pyproject.toml` 含 `readme` 字段导致 `hatchling` build 失败
- 修复实时行情走东财被反爬导致连接断开，改走腾讯/新浪/同花顺
- 修复搜索 `onblur` 比 `onclick` 先执行导致下拉菜单提前关闭
- 修复 `panel-resizer` 超出边界遮挡右侧 Tab 点击区域
- 修复 `quick-action-bar` 定位遮挡右侧面板
- 修复导出模式下消息无法选中（改为事件委托）
- 修复 SSE `complete` 事件缺少 token 字段导致前端显示"undefined"
- 修复自选股行情因浏览器 CORS 限制无法加载（改走后端代理）
- 修复 LLM 认证：旧网关路径 `/llmadapter` 失效，改用 `/llmratelimit/v3/anthropic` + SSO Cookie

### 文档
- `README.md`：真实启动方式，去掉 Docker/PG/Redis 依赖说明
- `docs/0014-启动与排错指南.md`：10 个常见问题 + 8 章详细说明
- `.env.example`：每个配置项详细注释 + 3 种使用场景
- `AGENTS.md`：AI Coding 助手规范（默认分支 master、禁止合规限制等）

---

## [0.0.1] - 2026-05-13

### 新增
- 项目初始化（`packages/server/` 结构、`pyproject.toml`、基础目录）

---

*此文件由开发者和 AI 协作维护。每次 push 必须同步更新。*
