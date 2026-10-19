# econ.socialdog.cn

现有 Caddy 网关接入独立容器，单进程、SQLite，不发布宿主机端口。镜像仅包含程序和构建后的前端；真实数据和模型密钥不进入构建上下文或 Git。

在项目根目录构建前端后，把源码和 `frontend/dist` 传到服务器的独立 release 目录。使用服务器已缓存的 Python 3.13.11 基础镜像，先运行完整后端测试，再构建同一版本的生产镜像：

```sh
docker build --target test -f deploy/Dockerfile --build-arg SOURCE_REVISION="$REVISION" .
docker build --target release -f deploy/Dockerfile --build-arg SOURCE_REVISION="$REVISION" -t "socialdog-econ:$REVISION" .
```

大陆服务器可添加 `--build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple` 加速依赖下载。

`/opt/econ-agent/data` 保存同源完整包 `history_202303_full`、物理截断包 `history_202303_asof_0315` 和独立核对库 `history_202303_reference/reference.sqlite`；完整包用于后台更改 T。保持三个目录同级。首次上线沿用现有 T=2023-03-15、N=5；正式题库由管理员在后台发布。

`/opt/econ-agent/workspace` 为容器 UID/GID 10001:10001 的私有可写目录（0700），`/opt/econ-agent/model.env` 为同一用户的私有配置（0600），格式同根目录 `.env.example`。模型密钥后台保存后优先使用 `workspace/.socialdog-secrets/deepseek.json`，无需重启。

```sh
export SOCIALDOG_IMAGE="socialdog-econ:$REVISION"
docker compose -f deploy/compose.yaml run --rm --no-deps workspace socialdog-api init-admin --database /workspace/workspace.sqlite --username admin --display-name 管理员 --public-origin https://econ.socialdog.cn
docker compose -f deploy/compose.yaml up -d
```

只在空数据库运行 `init-admin`，把一次性邀请私下交给账号本人设置密码。不要在终端参数里传密码或密钥。生产服务始终使用 Secure cookie。

网关 `/opt/anju/deploy/Caddyfile` 新增：

```caddyfile
econ.socialdog.cn {
    encode zstd gzip
    reverse_proxy socialdog-econ:8000
}
```

先使用现有容器 `caddy validate --config - --adapter caddyfile` 验证完整候选配置，再原地写入宿主机 Caddyfile。该文件通过单文件 bind mount 挂载，不要替换 inode。当前网关为 Caddy 2.10.2 且关闭管理 API，不支持通过 SIGUSR1 重载（该能力从2.11才提供）。配置验证后使用 `docker restart --timeout 10 anju-caddy-1` 加载新域名，会造成短暂网关访问中断；不重启业务应用容器。Caddy 自动签发并续期证书。

验收：容器 healthy、HTTPS 正常、未登录 `/api/me` 返回401、邀请有效、真实数据边界与 DeepSeek 连接正常；同时核对原有域名仍可访问。不要使用 `curl -k` 作为 HTTPS 验收。
