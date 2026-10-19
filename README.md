# SocialDog Agent

用自然语言查询门店经营数据的研究助理。提供本地网页、多轮问答、只读统计工具、数据版本和回答依据。模型使用 DeepSeek Responses API。

## 当前功能

- 查看数据表、字段、覆盖时间与数据缺口。
- 订单聚合：按总计、按日/周/月的时间序列、小时、星期、配送方式、销售来源、是否用券、是否用钱包。每组附带实际天数（日历天、有订单的天）和日均，周、月会标出是否完整。
- 商品级聚合（`query_products`）：按商品或商品+规格汇总记录件数、含该商品的订单数、明细金额和单件净额，可按日/周/月展开。
- 核查候选研究因素，说明能做什么、缺什么。
- 历史数据模式：27 列历史交易表作为 agent 的数据源，使用按订单数的披露规则，可查询商品、时间序列和数据质量，并能从项目单价重建历史调价事件，给出调价前后的描述性对比。
- `--as-of` 截断：建快照或回测包时物理删除截断日之后的数据，用于样本外测试。
- 自动评测：`eval_runner.py` 把 golden 用例交给真实 DeepSeek agent 回答，自动比对工具参数、工具结果和回答中的数字，只把推理、口径和局限性留给人工评审。
- 回答生成后自动核对数字：回答里的每个数字都必须能在本轮工具结果中找到；找不到时要求模型改写一次，仍找不到就在回答旁标出。
- 保存会话、显示执行进度、查看本轮工具参数和结果。
- 邀请制研究工作台：独立作答与测评、结构化问题反馈、研究想法、管理员派题/复核/按人汇总、测评额度预留和题库版本锁定。部署及角色规则见 [多人工作台部署](docs/多人工作台部署.md)。
- 读取官方飞书导出的本地核验版本，每题锁定一个只读快照。

储值、留存、实验分析、回归、图表及完整研究报告仍受数据或实现限制。调价前后对比是描述性的，不是价格弹性；没有成本数据，利润不可计算。模型不能用文字补造不存在的分析结果。飞书入口读取已导出的本地文件，不是实时飞书 API。

## 启动

需要 macOS 或 Linux、Python 3.10 以上。建议在服务器使用 Python 3.12。

```sh
git clone https://github.com/cjlyuppp/socialdog-agent.git
cd socialdog-agent
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m chat_agent --setup
```

API Key保存在本机 `.env`，也可通过环境变量配置；格式见 `.env.example`。真实营业数据、快照、聊天记录和密钥均不随仓库发布。

取得并在本机保存授权的完整飞书 `.base` 导出后，建立分析快照：

```sh
python -m socialdog_agent.feishu_source --import-base /实际路径/完整来源.base
python -m chat_agent --check
python -m web_agent --no-open
```

打开 `http://127.0.0.1:8765`。没有导入数据时网页可以启动，状态会提示数据源未就绪；此时不能进行真实业务查询。服务器上的浏览器访问方式见 [服务器部署说明](docs/legacy-deployment.md)。

macOS也可双击 `launchers/启动网页研究助理.command`。启动脚本优先使用项目虚拟环境。

## 历史数据模式与评测

```sh
# 1. 生成回测包。加 --as-of 会物理删除该日之后的订单，用于样本外测试
python -m historical_backtest build --source /实际路径/历史交易表.xlsx --output-dir outputs/history_full
python -m historical_backtest build --source /实际路径/历史交易表.xlsx --output-dir outputs/history_asof --as-of 2023-03-15
python -m historical_backtest verify --package outputs/history_full

# 2. 用历史数据提问（命令行或网页），每组至少 5 个订单才披露
python -m chat_agent --history-package outputs/history_full --question "历史上哪些商品调过价？"
python -m web_agent --history-package outputs/history_full --history-min-orders 5 --no-open

# 3. 用真实模型跑 golden 用例并自动判分
python -m eval_runner --package outputs/history_full --output-dir outputs/eval_$(date +%Y%m%d)
python -m eval_runner --package outputs/history_full --only P01,P02 --repeat 3 --output-dir outputs/eval_price
```

`eval_runner.py` 对每个用例自动检查四项：是否用 golden 参数调用了对应工具，工具结果是否与 golden 一致，回答是否写出了关键数字，回答里是否有工具结果中找不到的数字。输出 `results.jsonl`、`summary.json`，以及给人工评审用的 `review_sheet.csv`。人工只需按表中的核查要点，给推理过程、口径说明和局限性说明打分。

用例分为：D（数据描述）、A（聚合）、T（周/月时间序列）、P（调价事件）、Q（数据质量）、U（不能开展的分析）。其中 P01、P02 由回测包按项目单价自动生成：P01 列出全部持续调价，P02 针对第一次降价，问销量和收款有没有提升、利润如何。

两套数据的字段与口径对照见 [docs/口径对照.md](docs/口径对照.md)。用历史数据检查回答时，请以同一口径的工具结果为准。

## 模型配置

`.env` 中的 `DEEPSEEK_REASONING_EFFORT` 控制默认思考强度（none/low/high/max），默认 `low`；旧的minimal/medium分别对应low/high。多人工作台可在提问或独立作答时选择 Flash/V4 Pro 及思考强度，实际配置随任务保存。核心提示词只保留 8 条通用原则；与数据集相关的口径说明放在各数据源的工具说明和工具结果中。日数、日均、占比、变化率等派生数字由工具计算。

多人工作台管理员可在“管理后台 → 模型服务”验证并保存 DeepSeek API Key。密钥保存在服务器私有文件中，不回显；新任务立即使用，无需重启。未在后台配置时兼容本机 `.env`，具体优先级与备份位置见 [部署说明](docs/多人工作台部署.md#账号与模型)。

## 数据与研究口径

- 原始业务数据留在本机；外部模型只接收问题、有限历史语境、结构说明和允许披露的聚合结果。
- 当前订单工具要求每组至少20位可确认会员；不满足条件的分组不展示，只返回被隐藏的组数。未展示不代表零。
- 历史数据模式没有顾客 ID，改为每组至少 N 个订单（默认 5）。该规则只用于可信研究组。
- 钱包支付不等于充值或储值用户；订单数不等于商品件数；支付净额不等于利润或银行到账。
- 历史27列交易表不能直接替代飞书快照或完整研究工作簿；保留未知退款和顾客标识的空值。两套口径的差异见 [docs/口径对照.md](docs/口径对照.md)。
- 原始文件和数据版本由部署者单独提供。GitHub仓库只负责分发程序，不能代替数据交接。

## 验证

以下测试使用人工构造的数据，不需要API Key，也不会调用外部模型：

```sh
python -m unittest discover -s tests -v
```

## 多人研究工作台

`frontend/` 包含登录/邀请、待办、研究对话、数据核对、测评、研究想法和管理后台。FastAPI 后端使用 SQLite 保存账号、会话、任务、反馈、想法、派题、题库版本、评分及逐次模型用量。每位成员同时最多一个任务，提问和测评在调用模型前原子预占每日配额。

安装后提供 `socialdog-api`、`socialdog-chat`、`socialdog-web`、`socialdog-history`、`socialdog-eval` 命令；也可使用 `python -m api_server` 等模块入口。后端建议使用 Python 3.12。

```sh
python3.12 -m venv .venv-api
. .venv-api/bin/activate
python -m pip install -r requirements.txt
cd frontend
npm ci
npm run build
cd ..

# 从授权的27列交易表分别构建全量包和物理截断包，T由研究负责人确定
socialdog-history build --source /实际路径/历史交易表.xlsx --sheet 交易数据 --output-dir outputs/history_full
socialdog-history build --source /实际路径/历史交易表.xlsx --sheet 交易数据 --as-of 2023-03-15 --output-dir outputs/history_cut
socialdog-api build-reference --package outputs/history_full --output outputs/reference/reference.sqlite --cutoff 2023-03-15

# 仅空数据库可创建首位管理员；在浏览器中由本人设置密码
socialdog-api init-admin --username carrey --display-name Carrey --public-origin http://127.0.0.1:8000
socialdog-api serve --history-package outputs/history_cut --reference-db outputs/reference/reference.sqlite --public-origin http://127.0.0.1:8000 --local-http
```

日期是命令示例，不能替代正式截断日的研究决定。`--sheet` 须明确指定与27列原始字段一致的工作表，不自动转换其他清洗表。

打开 `http://127.0.0.1:8000`，使用 `init-admin` 输出的一次性邀请设置密码。没有默认账号或默认密码。`--local-http` 仅允许本机 HTTP 开发，HTTPS 部署使用默认 Secure cookie。一次只运行一个后端进程；重启时未完成任务标记为失败，不自动重复调用模型。

首次部署不传 `--question-bank` 时题库为空，之后可在管理后台编辑草稿并发布。题库与 T/N、研究规则、审核权限及额度均有管理模块；可选的提交后数据核对和 A/B 评分默认关闭。正式题库、数据准备、配额和用量口径见 [多人工作台部署说明](docs/多人工作台部署.md)，接口见 [接口约定](docs/接口约定.md) 和 [OpenAPI](docs/openapi.json)。前端独立开发方法见 [frontend/README.md](frontend/README.md)。

## 目录

```text
src/                 Python 命令模块与 socialdog_agent 包
frontend/            网页源码、组件和前端测试
tests/               Python 单元与集成测试
docs/                接口、部署和数据口径说明
  archive/           历史交接文档
deploy/              econ.socialdog.cn 容器与现有 HTTPS 网关接入
launchers/           macOS 双击启动器
outputs/             私有数据包和评测产物，不进入 Git
research_local/      私有数据库、会话、密钥和审计记录，不进入 Git
```

安装后在项目根目录运行 `socialdog-api` 等命令，或使用 `python -m api_server`。开发验证可用 `PYTHONPATH=src python -m unittest discover -s tests -v`；从旧目录结构升级时须重新安装项目。

## 主要文件

| 路径 | 用途 |
| --- | --- |
| `src/api_server.py` | 多人研究工作台启动及管理入口 |
| `src/web_agent.py` | 单人原型启动入口 |
| `src/chat_agent.py` | 配置检查、密钥设置和命令行提问 |
| `src/socialdog_agent/` | 模型适配、工具调用、网页、会话和来源管理 |
| `src/research_assistant.py` | 只读分析快照及订单聚合 |
| `src/feishu_daily_clean.py` | 官方飞书导出文件的解码与清洗 |
| `src/historical_backtest.py` | 历史数据清洗、`--as-of` 截断、调价事件重建、golden 用例与核验 |
| `src/socialdog_agent/history_tools.py` | 历史数据模式的 agent 工具与订单数披露规则 |
| `src/socialdog_agent/grounding.py` | 回答数字与工具结果的自动核对 |
| `src/eval_runner.py` | 用真实模型跑 golden 用例、自动判分并生成人工评审表 |
| `docs/口径对照.md` | 飞书口径与历史口径的字段对照 |

`web_agent.py` 是保留的单工作区本地原型，不提供多人账号隔离，仅供可信研究组通过本机或 SSH 转发使用。多人使用请运行 `socialdog-api serve`；公网部署使用 HTTPS 反向代理，当前服务器的容器接入方式见 [deploy/README.md](deploy/README.md)。
