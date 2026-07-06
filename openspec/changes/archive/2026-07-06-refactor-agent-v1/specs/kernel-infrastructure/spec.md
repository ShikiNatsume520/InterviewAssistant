## ADDED Requirements

### Requirement: 横切关注点统一收口到 kernel
系统 SHALL 把跨 agent 共享的横切关注点（配置、路径、LLM 工厂、embedding、持久化、日志、HITL 契约）集中到 `src/kernel/`，作为唯一真相源。各 agent 包 SHALL 通过 `from kernel.<module> import ...` 引用，不得在 agent 包内重新定义这些横切逻辑。

#### Scenario: agent 引用 LLM 工厂
- **WHEN** 任一 agent 节点需要构造 LLM 实例
- **THEN** 它 SHALL 调用 `kernel.llm.get_chat_model(...)`，而非各自 `from src.client import get_chat_model` 或内联 `init_chat_model`

#### Scenario: 数据路径不再各处重算
- **WHEN** 任一 agent 需要访问 markdown 知识库、Chroma 或 index.md 路径
- **THEN** 它 SHALL 从 `kernel.paths` 导入统一常量，不得用 `Path(__file__).parent.parent...` 重复推算

### Requirement: 持久化路径唯一真相源
系统 SHALL 在 `kernel/persistence.py` 定义 checkpointer 与 store 的数据库路径常量与工厂函数。Studio（`langgraph dev`）与 FastAPI server SHALL 引用同一组路径常量，不得在 `checkpointer.py` 或 `server.py` 中硬编码不同的 db 文件名。

#### Scenario: Studio 与 server 共用 checkpointer
- **WHEN** `langgraph dev` 启动且 `server.py` 运行
- **THEN** 两者 SHALL 连接同一 checkpointer db 文件路径（来自 `kernel.persistence.CHECKPOINT_DB_PATH`）

#### Scenario: 不存在孤儿 db 文件
- **WHEN** 检查项目根目录的持久化文件
- **THEN** SHALL 只存在 `kernel/persistence.py` 声明的 checkpointer 与 store db 文件，不得有 `sqlite_checkpoints.db` / `state_db.sqlite` / `sqlite_store.db` 三套并存

### Requirement: Chroma collection 名唯一定义
系统 SHALL 在 `kernel/embedder.py` 唯一定义 `COLLECTION_NAME` 常量。`rag_agent` 与 `index_agent` SHALL 从 `kernel.embedder` 导入该常量，SHALL NOT 在各自模块内重复定义。

#### Scenario: collection 名无重复定义
- **WHEN** 在 `src/` 下搜索 `COLLECTION_NAME =` 赋值
- **THEN** SHALL 仅在 `kernel/embedder.py` 出现，`rag_agent/` 与 `index_agent/` 内不得重复定义

### Requirement: 循环依赖断开
系统 SHALL 保证 `import agents.main.graph` 不触发循环导入错误。`agents/main/__init__.py` SHALL 最小化，不得 eager import `graph` 模块。

#### Scenario: 顶层 import 子图不触发环
- **WHEN** `agents/main/tools/research_agent.py` 在模块顶层执行 `from agents.research.graph import graph`
- **THEN** 该 import SHALL 成功完成，不抛 `ImportError`/`RecursionError`
