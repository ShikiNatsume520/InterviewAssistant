# 单人私有 Docker 部署实现计划

## 1. 部署目标

本轮只满足作者本人访问，不向互联网公众提供服务：

- 应用打包为可复现的 Linux Docker 镜像；
- 代码放在 GitHub 私有仓库，镜像放在 GHCR 私有仓库；
- 在一台 VPS 或长期在线主机上运行单实例容器；
- 应用端口不向公网开放；
- 只有作者本人加入的 Tailscale 设备能够访问；
- `data/` 持久化并可备份；
- 生产环境关闭开发人员登录入口。

推荐链路：

```text
个人电脑/手机浏览器
        │ Tailscale 私有网络 + HTTPS
        ▼
VPS 上的 Tailscale Serve
        │ localhost
        ▼
Interview Assistant Docker 容器（单实例、单 worker）
        │
        ▼
/srv/interview-assistant/data
```

Tailscale 官方支持把 Docker/主机加入 Tailnet：<https://tailscale.com/docs/features/containers/docker>。

## 2. 本轮安全边界

### 必须完成

- `IA_DEV_MODE=false`，后端开发登录接口保持 404；
- `IA_COOKIE_SECURE=true`；
- VPS 防火墙不开放应用端口 `8000`；
- 应用只绑定到 localhost，或只允许 Tailscale 代理访问；
- 不把 `.env`、API Key、数据库、日志和用户数据写入镜像或 Git；
- 容器使用非 root 用户；
- 仅运行一个容器副本、一个 Uvicorn worker；
- `/app/data` 使用宿主持久化挂载；
- 设置请求体和 Markdown 上传硬上限；
- 完成备份与恢复验证。

### 本轮可以暂缓

因为应用不对公众开放，以下项目可以暂缓：

- 公网匿名用户限流；
- Redis 共享限流；
- 注册登录系统；
- 多实例任务协调；
- 完整 Provider SSRF 防护；
- PostgreSQL、对象存储和托管向量库迁移；
- Kubernetes 和自动扩缩容。

但应保留当前的 HTTPS Provider 校验。未来只要开放给第二个不完全受信任的用户，就必须重新实施 Provider 白名单、SSRF 防护、限流、配额和游客数据清理。

## 3. 精简实施步骤

### 步骤 1：容器化

新增：

- `Dockerfile`：Node 前端构建 + Python 运行时的多阶段镜像；
- `.dockerignore`：排除 `.env`、`.git`、`data/`、日志、缓存、`.venv` 和 `node_modules`；
- 容器启动脚本：初始化数据目录后，以单 worker 启动 Uvicorn；
- `compose.yaml`：应用、持久卷、环境变量和健康检查。

镜像要求：

- 平台为 `linux/amd64`；
- 前端构建产物包含在镜像中；
- 以非 root 用户运行；
- `CMD` 使用平台 `$PORT`，默认 `8000`；
- 不使用 `--reload`；
- `--workers 1`；
- 健康检查访问 `/health`。

公共知识种子放到 `/app/data-seed`。仅当挂载的 `/app/data` 为空时初始化，已有数据绝不覆盖。

验收：

1. 本地构建镜像成功；
2. 用空卷启动成功；
3. 上传简历、创建 Thread 和知识后重启容器；
4. 数据与 checkpoint 仍可恢复；
5. 镜像内不存在 `.env` 和真实 API Key。

### 步骤 2：私有访问

主机安装 Tailscale并加入作者自己的 Tailnet：

- Docker 应用端口只映射到 `127.0.0.1:8000`；
- Tailscale Serve 把 Tailnet HTTPS 地址代理到本机 `8000`；
- Uvicorn 只信任来自本机代理的 forwarded headers；
- VPS 安全组/防火墙不开放 `8000`；
- Tailnet ACL 只允许作者的账户或指定设备访问该服务。

生产环境：

```env
IA_DEV_MODE=false
IA_COOKIE_SECURE=true
```

验收：

1. 未加入 Tailnet 的设备无法访问；
2. 已授权设备可通过 HTTPS 访问；
3. 开发人员入口不显示；
4. `/v1/identity/developer` 返回 404；
5. API Key、Cookie 和 SSE 均只走 HTTPS；
6. 中断、恢复、RAG、简历和深研流程正常。

### 步骤 3：镜像发布

使用 GitHub 私有仓库和 GitHub Container Registry：

```text
GitHub main
   │ GitHub Actions
   ├─ ruff
   ├─ mypy --strict
   ├─ pytest
   ├─ frontend build
   └─ docker buildx
        ▼
ghcr.io/<user>/interview-assistant:<git-sha>
```

镜像至少发布两个不可混淆的标签：

- 版本标签，如 `v1.0.0`；
- 完整或短 Git SHA。

生产部署使用版本标签或 digest，不依赖可变的 `latest`。GitHub 官方的 GHCR 使用说明：<https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry>。

验收：

1. GitHub Actions 全部检查通过才发布镜像；
2. GHCR 镜像默认为私有；
3. VPS 使用只读 `read:packages` 凭据；
4. VPS 能拉取指定版本并启动；
5. 上一个版本镜像仍保留，可快速回滚。

### 步骤 4：部署、备份与回滚

VPS 建议目录：

```text
/srv/interview-assistant/
├─ compose.yaml
├─ .env                 # 权限 600，不进 Git
├─ data/                # 持久数据
└─ backups/             # 本机临时备份，另存异地副本
```

部署流程：

1. 拉取指定镜像版本；
2. 备份当前 `data/`；
3. 停止旧容器；
4. 启动新容器；
5. 检查 `/health`；
6. 执行关键用户故事冒烟测试；
7. 失败则回滚旧镜像，必要时恢复数据备份。

备份要求：

- 每日备份完整 `data/`；
- 备份复制到另一台设备或对象存储；
- 至少保留 7 个日备份；
- 每月实际执行一次恢复演练；
- 备份记录对应的 Git commit 和镜像版本。

## 4. 备选方案：Cloudflare Tunnel + Access

如果不希望每台访问设备安装 Tailscale，可以：

- 容器仍只监听 localhost；
- 使用 Cloudflare Tunnel 建立出站隧道，不开放 VPS 入站应用端口；
- 使用 Cloudflare Access 保护整个域名；
- Allow 规则只包含作者的完整邮箱地址，不允许 `Everyone` 或所有有效邮箱；
- 设置较短的 Access Session；
- 不为任何 `/v1/*` 路径设置 Bypass。

Cloudflare Access 支持按具体邮箱建立 Allow 策略，并且未匹配 Allow 的访问默认拒绝：<https://developers.cloudflare.com/cloudflare-one/access-controls/policies/>。

该方案的优点是浏览器无需安装 Tailscale；缺点是多一层外部身份和代理配置，错误的 Access/Bypass 规则可能意外暴露应用。因此单人、固定设备场景仍优先选择 Tailscale。

## 5. 不采用的方案

- GitHub Pages：不能运行 FastAPI，且会迫使前后端跨域；
- Cloud Run 等无状态 Serverless：本地 SQLite、Chroma 和 checkpoint 不适配实例销毁与扩容；
- Kubernetes：当前只需单实例，会徒增持久卷与状态协调成本；
- 直接开放 VPS 的 `8000` 端口并依赖 IP 白名单：家庭/移动网络 IP 可能变化，也缺少设备身份验证；
- 在应用内新增一套用户名密码：单人私有访问已有 Tailscale 身份层，无需重复实现认证系统。

## 6. 实施顺序与完成标准

按以下顺序实施，不跨步：

1. Dockerfile、`.dockerignore`、启动脚本和 Compose；
2. 本地容器持久化与重启验证；
3. Tailscale 私有访问验证；
4. GitHub Actions + GHCR；
5. VPS 部署、备份恢复和回滚演练。

最终完成标准：

- 非 Tailnet 设备无法访问；
- 开发模式关闭且接口返回 404；
- HTTPS、SSE 和 API Key 配置正常；
- 容器重启和升级不丢数据；
- 单 worker 运行；
- 能从 GHCR 指定版本部署，也能回滚上一版本；
- 完成一次真实备份恢复演练。
