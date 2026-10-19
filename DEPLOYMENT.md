# 服务器部署交接

本仓库提供程序，不包含API Key、营业数据、数据库、聊天记录、飞书文档地址或本机同步状态。请从项目负责人处另行取得授权数据，不要把这些文件提交到GitHub。

以下步骤针对保留的 `web_agent.py` 单工作区原型。多人账号、任务队列、数据核对及测评服务请使用 [多人工作台部署说明](docs/多人工作台部署.md)。

## 1. 安装程序

在Linux服务器上执行，Python需3.10以上：

```sh
git clone https://github.com/cjlyuppp/socialdog-agent.git
cd socialdog-agent
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest test_chat_agent test_research_assistant test_feishu_agent_source test_feishu_daily_clean test_factor_inspection test_web_agent test_historical_backtest -q
```

`fcntl`和`zoneinfo`来自Python标准库。运行环境需要可用的Asia/Shanghai时区数据库；使用未附带时区数据的精简镜像时，先安装操作系统的tzdata。

## 2. 配置模型

```sh
python chat_agent.py --setup
```

按提示在终端输入自己的DeepSeek API Key。也可用`.env.example`格式创建本机`.env`，并设置权限：

```sh
chmod 600 .env
```

环境变量优先于`.env`。`.env`只支持`DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`、`DEEPSEEK_TIMEOUT`、`DEEPSEEK_REASONING_EFFORT`，不要直接在其中写Shell命令。

## 3. 单独导入数据

负责人交接授权的完整官方飞书`.base`导出后，上传到服务器私有目录，例如`/srv/socialdog-data/latest.base`。该文件不能通过公开仓库传递。

```sh
python -m socialdog_agent.feishu_source --import-base /srv/socialdog-data/latest.base
python chat_agent.py --check
```

成功后会在`research_local/`生成本机快照、来源指针、匿名化密钥和审计文件。这个目录被Git忽略，服务账户需要读写权限。更新数据时重复导入新的完整导出；已有内容可复用快照，来源核验时间与业务数据截至时间分别保留。

仓库不包含本地桌面定时任务。程序不会自动登录飞书、下载来源或按21:00定时运行；若需要服务器自动同步，应由部署者另行配置合法的数据获取和调度流程。

独立的27列历史交易Excel不满足这一入口的来源结构。可以使用`historical_backtest.py build --source ... --output-dir ...`准备离线回测数据，但不能伪造成已支付标记、退款流水或会员ID。

## 4. 启动与访问

```sh
python web_agent.py --no-open --port 8765
```

服务绑定`127.0.0.1:8765`。在使用者自己的电脑执行SSH转发：

```sh
ssh -N -L 8765:127.0.0.1:8765 用户名@服务器地址
```

保持SSH连接，再用浏览器打开`http://127.0.0.1:8765`。如果本机8765端口已被占用，先换本地应用端口或停止冲突进程；本版本会严格校验浏览器Host/Origin，不能随意改成其他本地端口后假定正常工作。

当前所有会话属于同一研究工作区，尚无每位用户的账户和访问权限。公网域名部署需先完善认证、会话隔离、HTTPS反向代理和Host/Origin处理；直接修改监听地址不能完成这些工作。

## 5. 后台运行示例

可用systemd管理进程。以下假设程序在`/opt/socialdog-agent`，服务账户为`socialdog`；部署者先创建账户，并确保它能读取程序和`.env`、读写自己的`research_local/`。

```ini
[Unit]
Description=SocialDog research agent
After=network.target

[Service]
Type=simple
User=socialdog
WorkingDirectory=/opt/socialdog-agent
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/socialdog-agent/.venv/bin/python /opt/socialdog-agent/web_agent.py --no-open --port 8765
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

保存在服务器的`/etc/systemd/system/socialdog-agent.service`后执行：

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now socialdog-agent
sudo systemctl status socialdog-agent
```

迁移或备份时把`research_local/`作为一组整体处理，其中包含会话、快照、审计和匿名化密钥。不要在GitHub、日志截图或聊天中公开该目录及`.env`。
