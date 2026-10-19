# SocialDog Agent

用自然语言查询门店经营数据的研究助理。提供本地网页、多轮问答、只读统计工具、数据版本和回答依据。模型使用 DeepSeek Responses API。

## 当前功能

- 查看数据表、字段、覆盖时间与数据缺口。
- 按总计、小时、星期、配送方式和销售来源汇总订单。
- 核查候选研究因素，说明能做什么、缺什么。
- 保存会话、显示执行进度、查看本轮工具参数和结果。
- 读取官方飞书导出的本地核验版本，每题锁定一个只读快照。
- 历史交易数据的离线清洗和参考查询，入口为 `historical_backtest.py`；与网页默认数据源分开。

储值、留存、实验分析、回归、图表及完整研究报告仍受数据或实现限制。模型不能用文字补造不存在的分析结果。飞书入口读取已导出的本地文件，不是实时飞书 API。

## 启动

需要 macOS 或 Linux、Python 3.10 以上。建议在服务器使用 Python 3.12。

```sh
git clone https://github.com/cjlyuppp/socialdog-agent.git
cd socialdog-agent
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python chat_agent.py --setup
```

API Key保存在本机 `.env`，也可通过环境变量配置；格式见 `.env.example`。真实营业数据、快照、聊天记录和密钥均不随仓库发布。

取得并在本机保存授权的完整飞书 `.base` 导出后，建立分析快照：

```sh
python -m socialdog_agent.feishu_source --import-base /实际路径/完整来源.base
python chat_agent.py --check
python web_agent.py --no-open
```

打开 `http://127.0.0.1:8765`。没有导入数据时网页可以启动，状态会提示数据源未就绪；此时不能进行真实业务查询。服务器上的浏览器访问方式见 [服务器部署说明](DEPLOYMENT.md)。

macOS也可双击 `启动网页研究助理.command`。启动脚本优先使用项目虚拟环境。

## 数据与研究口径

- 原始业务数据留在本机；外部模型只接收问题、有限历史语境、结构说明和允许披露的聚合结果。
- 当前订单工具要求每组至少20位可确认会员；不满足条件的分组不展示，未展示不代表零。
- 钱包支付不等于充值或储值用户；订单数不等于商品件数；支付净额不等于利润或银行到账。
- 历史27列交易表不能直接替代飞书快照或完整研究工作簿；历史回测需使用独立入口，并保留未知退款和顾客标识的空值。
- 原始文件和数据版本由部署者单独提供。GitHub仓库只负责分发程序，不能代替数据交接。

## 验证

以下测试使用人工构造的数据，不需要API Key，也不会调用外部模型：

```sh
python -m unittest test_chat_agent test_research_assistant test_feishu_agent_source test_feishu_daily_clean test_factor_inspection test_web_agent test_historical_backtest -v
```

## 主要文件

| 路径 | 用途 |
| --- | --- |
| `web_agent.py` | 网页启动入口 |
| `chat_agent.py` | 配置检查、密钥设置和命令行提问 |
| `socialdog_agent/` | 模型适配、工具调用、网页、会话和来源管理 |
| `research_assistant.py` | 只读分析快照及订单聚合 |
| `feishu_daily_clean.py` | 官方飞书导出文件的解码与清洗 |
| `historical_backtest.py` | 历史数据离线清洗、参考查询和核验 |

网页当前是供可信研究组使用的单工作区原型，服务仅监听本机，未实现用户登录和多人会话权限隔离。现阶段在服务器上通过SSH转发使用；公网开放前需要部署者完善相应访问控制。
