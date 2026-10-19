# 交接说明（2026-10-19）

> 以下保留早期交接快照。当前功能、运行方式和权限规则以 `README.md`、`docs/多人工作台部署.md` 及代码为准。

## 当前状态

- 仓库：`cjlyuppp/socialdog-agent`，本地分支 `eval-readiness`，基于 `main` 的 `20095ad`。
- **所有改动都还没有 commit，也没有 push。** 共两轮改动，可以分成两个 commit：
  1. 测评准备：agent 接入历史数据、扩充工具、`--as-of` 截断、调价事件、自动评测、精简提示词。
  2. 多人网页前端：`frontend/` 目录，加上 `docs/接口约定.md`。
- 测试：Python 97 个，前端 9 个，全部通过；前端类型检查和生产构建通过。
- **还没有用真实 DeepSeek 跑过**，本机没有 API Key。

## 一、测评准备（Python 侧）

| 问题 | 做了什么 | 位置 |
| --- | --- | --- |
| agent 能回答的问题太少 | `query_aggregate` 增加按日/周/月、是否用券、是否用钱包；新增商品级工具 `query_products`；快照新增商品明细表 | `research_assistant.py`、`socialdog_agent/tools.py` |
| 历史数据进不了 agent | 新增历史数据模式（`--history-package`），披露规则改为每组至少 N 个订单（默认 5）；写了两套口径的字段对照 | `socialdog_agent/history_tools.py`、`docs/口径对照.md` |
| 没有时间截断 | `historical_backtest.py build --as-of` 和 `research_assistant.py build --as-of` 会物理删除截断日之后的数据；截断包不附带原始 xlsx | `historical_backtest.py`、`research_assistant.py` |
| 价格变化没用上 | 新工具 `history_price_events`：用项目单价重建调价事件，给出前后窗口对比；利润明确返回“不可得” | `historical_backtest.py`、`history_tools.py` |
| 用例没在真模型上跑过 | `eval_runner.py` 用真实 agent 跑 golden 用例并自动判分，另输出人工评审表；golden 用例从 33 个增加到 40 个（新增 T01/T02、A19、P01/P02） | `eval_runner.py` |
| 提示词过长、规则太多 | 核心原则缩减为 8 条，数据集相关说明移到各自的工具里；默认 `reasoning=low`；回答生成后自动核对数字，找不到来源时让模型重写一次 | `socialdog_agent/agent.py`、`grounding.py`、`llm.py` |

常用命令：

```sh
python historical_backtest.py build --source 历史表.xlsx --output-dir outputs/history_asof --as-of 2023-03-15
python historical_backtest.py verify --package outputs/history_asof
python chat_agent.py --history-package outputs/history_asof --question "历史上哪些商品调过价？"
python eval_runner.py --package outputs/history_asof --output-dir outputs/eval_smoke --only A01,T01,P01,P02,U08
```

需要注意：

- 快照版本升到 0.2.0，飞书快照的目录名带上了版本号，首次提问时会自动重建。已有的历史回测包（1.0）需要重新 build。
- 飞书商品明细的字段名是推测的（`历史商品名称`、`数量` 等）。导入真实数据后，先看 `describe_data` 返回的 `item_coverage.matched_item_fields`，确认匹配上了正确的字段。
- 不确定 `deepseek-flash` 是否接受 `reasoning.effort = "low"`。如果返回 400，在 `.env` 里改 `DEEPSEEK_REASONING_EFFORT`。

## 二、多人网页前端（`frontend/`）

- 技术栈：React 19 + Vite + TypeScript + Tailwind 4 + TanStack Query。按要求跳过了 Figma，直接写代码，配色沿用原有网页。
- 6 个页面：登录/邀请、研究对话、数据核对、测评、我的测评、管理后台。在模拟后端上已经完整跑通；在 1440 和 390 两种宽度下用 Chrome 检查过截图，没有横向滚动。
- 后端尚不存在。接口约定写在 `frontend/src/api/types.ts`，说明文档是 `docs/接口约定.md`。浏览器里的 MSW 模拟后端（`frontend/src/mocks/`）已经实现了这些规则，可以作为后端的行为参考：CSRF 校验、每人同时 1 个任务、个人配额与全站上限、只能打一次分、管理接口只对 admin 开放。
- 开发方式：`cd frontend && npm install && npm run dev`，开发模式默认使用模拟后端。模拟账号：`carrey / demo`（管理员）、`lin / demo`（研究员）。
- 接入真实后端：`VITE_USE_MOCK=0 VITE_API_PROXY=http://127.0.0.1:8000 npm run dev`。
- 模拟数据是合成的两年订单，截断日 2023-06-30，不是真实经营数据。
- GitHub Actions 已加入前端任务：类型检查、测试、构建。

## 下一步（按原计划）

1. **提交**：把两轮改动分成两个 commit，开 PR 合并。开 PR 之前，先和仓库作者确认他那边没有冲突的改动。
2. **真模型冒烟测试**：配置 Key，用一个截断包跑上面的 `eval_runner.py`。看三件事：自动通过率、自动核对数字时有没有误报、`reasoning=low` 下的 token 用量。
3. **P0 仓库整理**：目录搬到 `src/socialdog/...`，添加 `pyproject.toml` 和统一命令入口。注意：这一轮新增的文件也要一起搬。
4. **P2 后端**：用 FastAPI 按 `docs/接口约定.md` 实现。重点是：
   - 任务状态存进数据库，服务重启后不丢；
   - 提问前先检查配额，超额直接拒绝，不调用模型；
   - 每次模型调用都把 `usage` 写进库；
   - 数据核对接口只读 `reference.sqlite`，**并写一个测试保证它永远不会被注册成 agent 工具**。
5. **截断日 T 和披露门槛 N 请教授确定**，目前用的是占位值。

## 未决事项

- 测评题库现在只有模拟版（`frontend/src/mocks/db.ts` 里的 7 道题）。正式题库需要用真实历史数据的调价、新品、促销事件来出，答案由你验证。
- 历史数据模式的订单门槛默认是 5，是否合适需要和教授确认。
- 数据核对页只有表格，没有图表；需要的话可以在第二版加。
