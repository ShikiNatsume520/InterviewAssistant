## 1. R0 地基 — kernel 抽取 + 持久化统一 + 循环依赖拆解

> 阶段目标：建 `src/kernel/`，把散落的横切（paths/config/llm/embedder/persistence/logging）收口；`src/client.py` → `kernel/llm.py`、`agent/debug.py` → `kernel/logging.py`；`agents/main/__init__.py` 最小化拆环；持久化路径统一为单一真相源。验收：`langgraph dev` 起图 + 主图端到端跑通 + Studio 仍展开 rag/resume 子图。

- [x] 1.1 **协商 R0 边界**：文字陈述 R0 实现边界（哪些文件迁、哪些保留、`kernel` 各模块职责），与用户对齐后请求批准开发
- [x] 1.2 创建 `src/kernel/__init__.py`（空包占位，仅此一处）
- [x] 1.3 实现 `src/kernel/paths.py`：定义 `PROJECT_ROOT`、`MARKDOWN_DIR`、`CHROMA_PATH`、`INDEX_MD_PATH`，替换 `rag_agent/tools/retrieval.py`、`research_agent/graph.py`、`index_agent/graph.py` 中各自 `Path(__file__).parent...` 推算
- [x] 1.4 实现 `src/kernel/logging.py`：从 `src/agent/debug.py` 迁入 `dlog`/`slog`/`summarize_messages`/`_jsonable`/`_short`，零依赖（不 import 任何 `agents.*`）
- [x] 1.5 实现 `src/kernel/llm.py`：从 `src/client.py` 迁入 `get_chat_model`，暂保留硬编码模型名（R2 再 config 化），签名 `get_chat_model(model_name: str, tools=None)`；删除 `src/client.py`
- [x] 1.6 实现 `src/kernel/embedder.py`：从 `index_agent/tools/vectorstore.py` 抽出 `SiliconFlowEmbeddingFunction` + `get_embed_fn` 单例 + `COLLECTION_NAME` 常量（rag/index 共享，消除 `retrieval.py` 与 `vectorstore.py` 两处重复定义）；`vectorstore.py` 与 `retrieval.py` 改为 `from kernel.embedder import get_embed_fn, COLLECTION_NAME`
- [x] 1.7 实现 `src/kernel/persistence.py`：定义 `CHECKPOINT_DB_PATH`/`STORE_DB_PATH` 常量 + `get_checkpointer()`（async，迁 `checkpointer.py` 逻辑）+ `get_store()`（迁 `persistence.py` 逻辑）；统一路径，消除 `state_db.sqlite`/`sqlite_checkpoints.db`/`sqlite_store.db` 三套
- [x] 1.8 `src/agent/checkpointer.py` 改为薄封装，引用 `kernel.persistence.get_checkpointer`
- [x] 1.9 `src/agent/persistence.py` 删除（逻辑已迁入 kernel）；更新所有 `from agent.persistence import get_store` 为 `from kernel.persistence import get_store`
- [x] 1.10 全局替换 `from src.client import get_chat_model` → `from kernel.llm import get_chat_model`（graph.py / memory.py / resume_agent/graph.py / research_agent/graph.py）
- [x] 1.11 全局替换 `from agent.debug import ...` → `from kernel.logging import ...`（graph.py / memory.py / 三个 wrapper / resume/graph.py / research/graph.py / rag/graph.py）
- [x] 1.12 `src/agent/__init__.py` 最小化：移除 eager `from agent.graph import graph`，仅留必要导出；验证 `import agent.graph` 不连带拉起整图 LLM 实例化
- [x] 1.13 验证循环依赖已断：`python -c "import agent.graph"` 不报 `ImportError`/`RecursionError`
- [x] 1.14 **R0 验收**：`langgraph dev` 起图成功；Studio 中 main_agent 可展开 rag/resume 子图；FastAPI `/v1/chat` 端到端跑通一轮知识问答
- [x] 1.15 **R0 存档**：用户验收通过后，按用户指引 git 提交 R0

## 2. R1 registry — 可拔插布线 + 修复 research Studio 发现

> 阶段目标：引入 `agents/main/registry.py` 显式清单 + `routing.py` 查表路由；三 wrapper 统一为模块顶层 import 已编译子图（修复 `research_agent` 惰性 getter 的 Studio 展不开 bug）；`build_main_graph` 遍历 REGISTRY 布线。验收：Studio 首次展开 research 子图 + SSE 端到端跑通。

- [x] 2.1 **协商 R1 边界**：陈述 `SubAgentMeta` 结构、`REGISTRY`/`ROUTE_TABLE` 派生关系、wrapper 顶层 import 改造范围，与用户对齐后请求批准
- [x] 2.2 创建 `src/agent/registry.py`：定义 `SubAgentMeta`（TypedDict：`name`/`tool`/`node`/`route_key`）+ `REGISTRY: list[SubAgentMeta]` 显式清单（先登记 rag/resume/research 三条）+ `ROUTE_TABLE`（`{m.tool.name: m.route_key}`）+ `ALL_TOOLS`（`BASIC_TOOLS + [m.tool for m in REGISTRY]`）
- [x] 2.3 创建 `src/agent/routing.py`：迁入 `route_after_chat`，改为 `return ROUTE_TABLE.get(tool_name, "tools_node")` 查表，删除 if-elif 链
- [x] 2.4 改造 `src/agent/tools/research_agent.py`：删除 `_get_research_graph()` 惰性函数，改为模块顶层 `from research_agent.graph import graph as research_graph`，函数体裸名 `await research_graph.ainvoke(...)`
- [x] 2.5 确认 `src/agent/tools/rag_agent.py`、`resume_agent.py` 已是顶层 import（无需改），仅在 registry 接入时验证不退化
- [x] 2.6 改造 `src/agent/graph.py`：`build_main_graph` 遍历 `REGISTRY` 做 `add_node`/`add_edge`/`path_map`；删除手写的三条 `add_node`/`add_edge` 与 `path_map` 字面量；`chat_node` 的 `bind_tools(ALL_TOOLS)` 改引用 registry 的 `ALL_TOOLS`
- [x] 2.7 验证 Studio 子图发现：编译主图后断言 `compiled.nodes["research_agent"].subgraphs` 非空（可用原型脚本模式或 Studio 实际打开验证三个子图均可展开）
- [x] 2.8 **R1 验收**：Studio 中 main_agent 可展开 rag/resume/research 三种子图（research 为首次修复）；`/v1/chat` 端到端跑通知识问答 + 简历优化 + 深研 HITL 流程
- [x] 2.9 **R1 存档**：用户验收通过后 git 提交 R1

## 3. R2 模型配置化 + interrupt 契约固化

> 阶段目标：`"deepseek-v4-flash"` 等 5+ 处魔法字符串收进 `kernel/config.py`；`get_chat_model` 读 config；interrupt payload 用 Pydantic 固化进 `kernel/contracts.py`。验收：改 config 切模型生效 + HITL 流程 schema 校验通过。

- [x] 3.1 **协商 R2 边界**：陈述 config 实现方式（pydantic-settings vs 纯 os.getenv，见 design Open Questions）、模型名归类（chat/extraction/plan/distill/finalize）、interrupt schema 字段清单与现有 payload 的兼容性，与用户对齐后请求批准
- [x] 3.2 ~N/A~：选 os.getenv（无新依赖），无需安装 pydantic-settings
- [x] 3.3 实现 `src/kernel/config.py`：集中 `CHAT_MODEL`（chat_node）、`EXTRACTION_MODEL`（memory）、`RESUME_MODEL`（resume plan/react）、`RESEARCH_MODEL`（outline/distill/finalize）+ `DEFAULT_MODEL` 兜底 + provider 配置（DEEPSEEK_API_KEY/URL 从 env 读）
- [x] 3.4 改造 `kernel/llm.py:get_chat_model`：默认从 config 读模型名，签名改为 `get_chat_model(model: str | None = None, tools=None)`；`model=None` 时用 `DEFAULT_MODEL`
- [x] 3.5 替换 8 处硬编码（含 index_agent）：`agent/graph.py`、`agent/memory.py`、`index_agent/tools/llm_index.py`、`resume_agent/graph.py`(×2)、`research_agent/graph.py`(×3) 改为传 config 常量
- [x] 3.6 验证无硬编码模型名：在 `src/` 搜索 `deepseek-v4-flash` 仅在 `kernel/config.py` 出现
- [x] 3.7 实现 `src/kernel/contracts.py`：Pydantic schema `PlanConfirmPayload`/`StepConfirmPayload`/`OutlineConfirmPayload`/`ConnectivityCheckPayload` + `SuggestDecision`
- [x] 3.8 改造 `resume_agent/graph.py` 的 `plan_confirm_node`/`step_confirm_node`：`interrupt(...)` 传入的 dict 改用 contracts schema 构造 + `model_dump()`
- [x] 3.9 改造 `research_agent/graph.py` 的 `outline_confirm_node`/`connectivity_check_node`：同上用 contracts schema
- [x] 3.10 改造 `src/agent/server.py` 的 `_interrupt_payload`：用 contracts schema `model_validate` + `model_dump` 校验序列化；SSE `interrupt` 事件字段名与重构前兼容（`static/index.html` 不改可解析）
- [x] 3.11 **R2 验收**：改 config 模型名 → 重启 → 端到端验证模型切换生效；跑一次完整 HITL 流程（简历优化 plan/step confirm + 深研 outline confirm）验证 payload schema 校验通过且前端正常交互
- [x] 3.12 **R2 存档**：用户验收通过后 git 提交 R2

## 4. R3 物理重排 — 目录迁移到方案 C

> 阶段目标：`src/agent/` → `src/agents/main/`，四子 agent 归 `src/agents/<name>/`，`server.py` → `src/server/app.py`，新增 `src/cli.py`；更新 `langgraph.json`/`pyproject.toml`。验收：`langgraph dev` + tests/ 全绿。

- [x] 4.1 **协商 R3 边界**：陈述迁移批次顺序（建议：先建 `src/agents/` 骨架 → 逐个 agent 切 → 最后删旧 `src/agent/`）、import 全量更新范围、tests 同步更新，与用户对齐后请求批准
- [x] 4.2 创建 `src/agents/__init__.py`（最小化）与 `src/agents/main/__init__.py`（最小化）
- [x] 4.3 迁移 `src/agent/{graph,state,registry,routing,memory}.py` → `src/agents/main/`，拆分 `nodes/`（chat.py / memory.py）与 `tools/`（rag_agent.py / resume_agent.py / research_agent.py）子目录
- [x] 4.4 迁移四子 agent：`src/rag_agent/` → `src/agents/rag/`、`src/resume_agent/` → `src/agents/resume/`、`src/research_agent/` → `src/agents/research/`、`src/index_agent/` → `src/agents/index/`（含各自 state.py / graph.py / tools/）
- [x] 4.5 迁移 `src/agent/server.py` → `src/server/app.py`，挂载 `src/server/__init__.py`
- [x] 4.6 新增 `src/cli.py`：index_agent 主动唤醒命令入口（仅骨架 + 调用 index_agent 图，不实现额外功能——遵循不跨阶段占位）
- [x] 4.7 抽取 main prompts 到 `src/agents/main/prompts.py`：`_build_system_prompt()`（graph.py）+ `EXTRACTION_PROMPT`（memory.py）迁入，节点改 `from .prompts import ...`；含变量的封装为 `build_*()` 函数
- [x] 4.8 抽取 resume prompts 到 `src/agents/resume/prompts.py`：`PLAN_PROMPT` + `REACT_PROMPT` 迁入，封装为 `build_plan_prompt(resume, intent)` / `build_react_prompt(draft, plan_with_progress)` 函数
- [x] 4.9 抽取 research prompts 到 `src/agents/research/prompts.py`：`OUTLINE_PROMPT` + `DISTILL_PROMPT` + `FINALIZE_PROMPT` 迁入，含变量的封装为 `build_*()` 函数
- [x] 4.10 全量更新 import 路径：`from agent.` → `from agents.main.`、`from rag_agent.` → `from agents.rag.`、`from resume_agent.` → `from agents.resume.`、`from research_agent.` → `from agents.research.`、`from index_agent.` → `from agents.index.`
- [x] 4.11 更新 `langgraph.json`：五个图路径改为 `./src/agents/<name>/graph.py:graph`，checkpointer 改指 `./src/agents/main/checkpointer.py:generate_checkpointer`
- [x] 4.12 更新 `pyproject.toml`：`[tool.setuptools]` 包映射改为 `kernel`/`agents`/`server` + `package-dir` 指向 `src/<name>`；删除旧 `agent`/`rag_agent` 等映射
- [x] 4.13 更新 `tests/`：tests 无旧 import（grep 确认无残留），无需改动
- [x] 4.14 删除旧 `src/agent/`、`src/client.py`（R0 已删）、旧子 agent 目录（git mv 后旧目录已不存在）
- [x] 4.15 **R3 验收**：`langgraph dev` 起图加载五个图无 import 错误；`make lint`（ruff + mypy --strict + codespell）通过；`make test` 全绿；Studio 展开三个子图正常；`/v1/chat` 端到端跑通；各 agent `prompts.py` 存在且节点无内联 prompt 字符串常量
- [x] 4.16 **R3 存档**：用户验收通过后 git 提交 R3（可打 tag 标记重构完成）

## 5. 跨阶段约束（贯穿 R0–R3）

- [x] 5.1 每阶段实现前用「文字陈述 + 必要时原型」与用户协商，细节对齐且用户说"批准"后才动正式代码
- [x] 5.2 每阶段产出仅限本阶段实际需要的代码，不为本阶段不需要的后续文件/占位提前创建（硬性）
- [x] 5.3 每阶段满足 ruff + mypy `--strict` + codespell + 测试通过才提验收
- [x] 5.4 未验收不存档，未指引不自行 git 提交
