## Why

MVP 已完成 Phase 0–6（主图 + rag/resume/research/index 四子 agent + FastAPI SSE 端到端 + 长期记忆 + HITL 深研），但代码在六处横切关注点上严重耦合，正在阻碍后续迭代（多用户隔离、多模型切换、新增子 agent）：持久化路径三套并存且 Studio 与 server 不同库、模型名硬编码 5+ 处、主图无注册表导致加一个子 agent 要改四处、`research_agent` wrapper 用惰性 getter 使 Studio 无法展开其子图、子图之间直接 import 内部模块（rag→index_agent 内部、resume→主图 wrapper、research→index_agent 图）、import 风格混用 `src.client` 与裸包名。本次重构在不改变已验收功能行为的前提下，系统性收敛横切关注点并恢复可拔插 registry，为后续扩展打地基。

## What Changes

- **新增 `src/kernel/` 横切关注点层**：把散落在各 agent 的 config / paths / llm / embedder / persistence / logging / contracts 统一收口，成为唯一真相源。
- **统一持久化路径**：`checkpointer` 与 `store` 工厂收口到 `kernel/persistence.py`；Studio（`langgraph dev`）与 `server.py` 共用同一 db 文件路径常量，消除 `state_db.sqlite` / `sqlite_store.db` / `sqlite_checkpoints.db` 三套并存。
- **模型配置化**：`"deepseek-v4-flash"` 等 5+ 处魔法字符串收进 `kernel/config.py`；`get_chat_model` 读 config，按 config 模型名构造 LLM。**（选项 A：重启切换，不做 per-request 注入式多模型。）**
- **Prompt 与常量集中管理**：每个 agent 包建 `prompts.py` 集中管理 7 个散落 prompt（主图 system/memory、resume plan/react、research outline/distill/finalize），含变量的封装为 `build_*()` 函数；跨 agent 共享常量 `COLLECTION_NAME` 上提 `kernel/embedder.py` 唯一定义（消除 retrieval/vectorstore 两处重复）。
- **引入主图 registry**：新增 `agents/main/registry.py` 显式清单 + `routing.py` 查表路由；三个 wrapper 统一为"模块顶层 import 已编译子图 + 函数体裸名引用"；`build_main_graph` 遍历 REGISTRY 布线。新增子 agent 降至"一模块 + 清单一行"。
- **修复 Studio 子图发现**：`research_agent` wrapper 从惰性 `_get_research_graph()` 改为模块顶层 import，使 Studio 能展开其子图内部节点（其余两 agent 已是顶层 import，不退化）。
- **固化 interrupt 协议**：用 Pydantic schema 在 `kernel/contracts.py` 定义 `{decision, suggestion}` / `"approve"` / `"reject"` 等 HITL payload 契约，server 序列化与子图 interrupt 共用同一 schema。
- **统一 import 风格**：消除 `from src.client import ...`，统一以项目根为工作目录的裸包名 import（`from kernel.llm import ...`）；`agent/__init__.py` 最小化以拆循环依赖。
- **BREAKING：物理重排目录**：`src/agent/` → `src/agents/main/`，四个子 agent 归 `src/agents/<name>/`；更新 `langgraph.json` 图发现路径与 `pyproject.toml` 包映射。所有 import 路径随之变更。

## Capabilities

### New Capabilities
- `kernel-infrastructure`: 横切关注点统一层（config / paths / llm factory / embedder / persistence / logging / contracts），为所有 agent 提供唯一真相源
- `agent-registry`: 主图子智能体可拔插注册——显式清单布线 + 静态 wrapper，兼顾 Studio 子图发现
- `model-config`: 模型名与 provider 配置化，集中管理 LLM 实例构造
- `interrupt-contracts`: 主图↔子图↔前端 HITL interrupt payload 的 Pydantic 契约固化
- `package-layout`: 统一的目录结构与 import 风格约定（项目根为工作目录、裸包名 import、agent 自包含）

### Modified Capabilities
<!-- 纯内部重构，无外部 spec 级行为变更；现有功能行为保持不变，故无 Modified Capabilities -->

## Impact

- **代码**：`src/` 全部 5 个 agent 包 + `src/client.py` 受影响；新增 `src/kernel/`；`langgraph.json`、`pyproject.toml` 包映射更新；`src/agent/server.py`、`src/agent/checkpointer.py` 改引用 `kernel/persistence.py`。
- **APIs**：FastAPI `/v1/chat` 端点行为不变（请求/响应/SSE 协议保持兼容）；interrupt payload 形状不变但经 Pydantic 序列化。
- **持久化**：Studio 与 server 将共用同一 checkpointer db 路径——**迁移期间旧 `state_db.sqlite` / `sqlite_store.db` 数据不自动迁移**（MVP 阶段可接受重置）。
- **依赖**：无新增第三方依赖（Pydantic 已为 FastAPI 间接依赖，`langchain-core` 已含）。
- **调试工具**：Studio 子图展开行为改善（research_agent 修复）；`langgraph dev` 热重载继续可用。
- **测试**：现有 `tests/` 中 `from agent.graph import graph` 等导入路径需同步更新。
