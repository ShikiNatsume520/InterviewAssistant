# Interview Assistant

基于 LangGraph、FastAPI 与 React 的面试准备助手。系统提供普通问答、RAG、深度研究、Markdown 简历管理与多智能体简历修改，并通过 Thread、checkpoint 和产品事件时间线实现会话隔离与中断恢复。

## 环境要求

- Python 3.11 或 3.12
- `uv`
- Node.js 与 npm
- Windows、Linux 或 macOS 桌面环境

## 安装

后端依赖统一由 `uv` 管理：

```shell
uv venv
uv pip install -r pyproject.toml
```

前端依赖：

```shell
cd frontend
npm install
```

项目不会自动安装依赖，也不要混用 `pip` 与 `uv`。

## 环境变量

复制 `.env.example` 为 `.env`，按需要填写：

```text
DEEPSEEK_API_KEY=...
DEEPSEEK_API_URL=https://api.deepseek.com/v1
IA_DEFAULT_MODEL=deepseek-chat

IA_DEV_MODE=false
IA_DEV_ACCESS_TOKEN=使用高熵随机值
IA_COOKIE_SECURE=false
```

游客通过浏览器配置自己的 OpenAI-compatible Provider。API Key 只保存在当前标签页的 `sessionStorage`，随模型请求经请求头传输，不进入 checkpoint、产品事件或应用数据库。

开发人员模式使用服务端 `.env`，主要用于本地调试和 LangGraph Studio。生产环境必须关闭 `IA_DEV_MODE`。

## 本地启动

### React 与 FastAPI 同源运行

先构建前端：

```shell
cd frontend
npm run build
cd ..
```

再启动 FastAPI：

```shell
uv run uvicorn server.app:app --host 127.0.0.1 --port 8000 --reload
```

浏览器打开 `http://127.0.0.1:8000`。FastAPI 会托管 `frontend/dist`；如果尚未构建，根路径返回明确的 `FRONTEND_NOT_BUILT` 错误，不再回退旧静态测试页面。

### LangGraph Studio

```shell
uv run langgraph dev
```

Studio 直接使用 `.env`，不依赖浏览器模型配置。`langgraph.json` 注册了 main、rag、resume、research 与 index 图。

## 测试与检查

```shell
uv run ruff check src tests
uv run mypy --strict src
uv run python -m pytest tests/unit_tests

cd frontend
npm run build
```

不需要主动运行拼写检查；功能、ruff、mypy strict 和 pytest 是本项目本地验证重点。

## 持久化数据

- `data/state/app.sqlite`：身份、Session 哈希、Thread、活动任务、操作幂等账本、产品事件、简历与知识库资源元数据。
- `data/state/checkpoints.sqlite`：LangGraph checkpoint。
- `data/state/store.sqlite`：LangGraph 长期记忆 Store。
- `data/markdown/`：知识 Markdown 与资源元数据。
- `data/chroma/`：向量数据。
- `data/index.md`：关键词检索索引。

上线前建议从干净的数据目录启动。备份时应保持 SQLite 主文件及 WAL 一致，并同时备份 Markdown、索引与向量目录。

## 生产部署检查清单

- 在反向代理上启用 HTTPS，并把 HTTP 重定向到 HTTPS。
- 设置 `IA_COOKIE_SECURE=true`。
- 设置 `IA_DEV_MODE=false`，不要向公网暴露开发人员登录。
- `.env` 不得打包进 `frontend/dist`，也不得提交到 Git。
- 只允许可信代理设置转发协议头，确保 FastAPI 能正确判断外部请求 scheme。
- 模型 Provider Base URL 使用 HTTPS；HTTP 只允许本机调试地址。
- 限制应用数据库和 `data/` 目录的文件权限，制定一致性备份策略。
- 反向代理不要缓冲 SSE，并为长任务配置合理的读取超时。
- 使用单进程部署。当前可取消任务注册表位于进程内；多 worker 部署需要外部任务协调机制，本版本不支持。
- 定期检查磁盘容量，尤其是 checkpoint、产品事件和向量库目录。

## 浏览器范围

第一版面向桌面版 Chrome、Edge 和 Firefox。暂不承诺 Safari 专项兼容、移动端布局或离线使用。
