# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目现状（重要）

**进度**：见 [docs/progress.md](docs/progress.md)。

## 常用命令

包管理使用 `uv`（仓库根有 `uv.lock`），也兼容 `pip`。

```bash
# 安装（含 LangGraph CLI，用于本地 server）
pip install -e . "langgraph-cli[inmem]"
# 或
uv venv && uv pip install -r pyproject.toml

# 启动 LangGraph Server（热重载，配合 LangGraph Studio 可视化调试）
langgraph dev

# 测试
python -m pytest tests/unit_tests/                          # 单元测试
python -m pytest tests/integration_tests                    # 集成测试（anyio + @pytest.mark.langsmith）
python -m pytest tests/unit_tests/test_configuration.py     # 单个测试文件
make test TEST_FILE=tests/unit_tests/test_xxx.py            # 通过 Makefile 指定文件

# Lint / 格式化（CI 会强制执行，本地应先跑）
make lint          # ruff check . + ruff format --diff + ruff check --select I + mypy --strict（针对 src/）
make format        # ruff format + ruff check --select I --fix
make lint_package  # 仅检查 src/
make lint_tests    # 仅检查 tests/（独立 mypy cache）
codespell --toml pyproject.toml   # 拼写检查，CI 对 README.md 与 src/ 强制
```

CI（[.github/workflows/unit-tests.yml](.github/workflows/unit-tests.yml)）在 Python 3.11/3.12 上运行 ruff + mypy `--strict` + codespell + pytest。提交前本地跑一遍 `make lint` 可避免 CI 失败。

## 架构关键点

### 入口与打包
- [langgraph.json](langgraph.json) 把图注册为 `main_agent`，指向 `./src/agent/graph.py:graph`，env 文件为 `.env`。
- [pyproject.toml](pyproject.toml) 把同一份 `src/agent` 同时映射为两个包：`agent` 和 `langgraph.templates.agent`。导入时用 `from agent.graph import graph`（测试中就是这么写的）。
- 运行时上下文通过 `Context`（TypedDict）+ `context_schema=` 暴露，可在创建 assistant 或 invoke 时覆盖（见 graph.py 注释链接）。

### 目标架构（设计文档，尚未实现）
设计文档定义的多智能体拓扑，后续实现时应遵循其约定：

- **主图 `MainState`**：`messages`（`add_messages` 累积）、`citations`、以及为每个子图预留的**隔离 I/O 槽位**（如 `rag_subgraph_input` / `rag_subgraph_output`、`research_subgraph_input` / `research_subgraph_output`）。主图与子图状态通过槽位交互，**不直接交叉污染**（状态隔离原则）。
- **动态子智能体注册（`AgentRegistry`）**：通过 `SubAgentMetaData` 注册 wrapper 节点函数、input/output 槽名；`generate_system_instruction()` 动态拼装注入 `chat_node` 的子智能体列表 Prompt；`build_main_graph()` 据此循环 `add_node` / `add_edge` 构建可拔插拓扑。
- **`chat_node` 结构化决策**：用 `with_structured_output(AgentAction)` 强制 LLM 在 `chat` / `tool` / `sub_agent` 三种 action 间决策，结果挂在临时 AI 消息的 `additional_kwargs["decision"]` 上，由 `tool_dispatcher_node` 解析并填充对应 input 槽，再由 `main_dynamic_router` 据"哪个 input 槽非空"路由到对应 wrapper 节点。
- **子图 1 RAG**：三路混合检索（向量/`index.json` 索引/本地 Markdown grep）→ 重合行区间去重合并 → 输出带 `[文件名](start_line~end_line)` 的 `citations_output`；得分全低于阈值时输出 `gap_topic`。
- **子图 2 Deep Research**：收到 `gap_topic` → `interrupt()` 挂起等用户批准（HITL）→ 获批后多轮 web 爬取 → 写入新 Markdown → 触发 Index Agent 重建 `index.json`。
- **子图 3 Memory**：静默提取用户画像/弱点，持久化到 Store。
- **Index Agent（后台 agent，不在主图流程中）**：定位为**数据导入触发的后台维护 agent**，与主图/对话流程**完全解耦**。仅在「通过非对话接口（如数据导入 API）导入新 Markdown」时自动唤醒，扫描文件、维护 `index.json` + Chroma 向量灌入。**主图不调用、不应调用 Index Agent**——主图只读 `index.json` 与 Chroma（通过 `rag_agent` 的检索工具），不触发索引重建。前端未实现前，提供一个**主动唤醒命令**（CLI / 脚本入口）手动激活它处理一批文件。Deep Research 写入新 Markdown 后重建索引，也走该命令/导入接口，而非主图节点。
- **存储层**：`SqliteSaver`（checkpoints，`sqlite_checkpoints.db`，支持 `thread_id` 状态回放/故障重放）、本地 SQLite `Store`（长期记忆）、本地 Chroma（向量库，`domain_kb` / `interview_kb` 两个 collection）。

### 增量开发路线（设计文档第 6 节）
Phase 1 混合 RAG 子图 → Phase 2 动态主图总线 → Phase 3 本地 SQLite 断点 → Phase 4 FastAPI 端到端（`/v1/chat`，SSE 流式）→ Phase 5 自主深研 HITL。每个 Phase 用 LangGraph Studio 可视化验证节点轨迹与中间状态。

## 开发约定

- **行级引用是硬约束**：知识库切片必须在 metadata 保留 `start_line` / `end_line`，检索结果必须附带文件名+行区间——这是设计文档反复强调的核心需求，实现 RAG 时不可省略。
- **LangGraph Studio 是首要调试工具**：`langgraph dev` 启动后可编辑历史 state、从任意节点重跑、查看子图内部变量。改图后热重载。
- **新增子智能体时**：按设计文档的注册模式，走 `AgentRegistry` + 预留 input/output 槽位 + wrapper 节点，而非直接在主图里堆节点——保证状态隔离与可拔插。
- **子智能体目录结构（硬性约定）**：每个子图 / 子 agent 的相关逻辑**全部集中到 `src/` 下独属文件夹**，不散落到主图或公共模块。例如 `rag_agent` 全部放 `src/rag_agent/`。文件夹内大致结构：
  - `graph.py` — 该智能体的图逻辑（节点、边、编译）。
  - `state.py` — 该智能体需要的状态及数据结构定义（TypedDict / Pydantic 等）。
  - `tools/` — 该智能体可使用的工具（按需分子模块）。
  - 其余辅助模块（如 `prompts.py`、`retrieval.py`）按需新增，但务必**自包含**在本文件夹内。
  - 主图 `src/agent/graph.py` + `src/agent/registry.py` 仅通过 wrapper 节点 + 槽位引用子图，不直接实现子图内部逻辑。
  - 命名：文件夹名即子 agent 名（`rag_agent` / `resume_agent` / `research_agent` / `memory_agent` 等），与 `AgentRegistry` 注册名一致。
  - **Index Agent 是例外**：它不进 `AgentRegistry`、不在主图流程中，是后台 agent（详见下文「Index Agent 定位」），但仍遵循本目录结构约定，放 `src/index_agent/`，外加一个可被导入接口 / CLI 唤醒的入口。
- **Index Agent 定位（后台 agent，不进主图）**：职责仅是「在导入新 Markdown 时整理更新 `index.json` + Chroma 向量灌入」。触发方式：① 前端就绪后，由**非对话的数据导入接口**自动唤醒；② 前端未实现时，提供**主动唤醒命令**（CLI / 脚本入口）手动激活处理一批文件。**主图不调用、不应调用 Index Agent**——主图只读 `index.json` 与 Chroma（经 `rag_agent` 检索工具）。Deep Research 写新 Markdown 后重建索引也走导入接口 / 命令，而非主图节点。
- Lint 配置见 [pyproject.toml](pyproject.toml) `[tool.ruff]`：启用 E/F/I/D/UP，google pydocstyle 约定，`tests/*` 放宽 D/UP。mypy 跑 `--strict`。
- `.env` 仅放 `LANGSMITH_PROJECT` 与各 provider API key（见 `.env.example`）；不要把密钥写进代码。


## 技术选型决策（已与用户拍板）

- **LLM provider**：OpenAI 兼容端点（`langchain-openai` 的 `ChatOpenAI`，`base_url` / `model` / `api_key` 走 `.env` 配置）。可对接 OpenAI / DeepSeek / Qwen / 智谱等任意 OpenAI 兼容服务。所有 `chat_node`、Memory、Deep Research 节点统一用此。
- **Embedding**：本地 `sentence-transformers`（中文语料优先 `bge-small-zh` / `bge-m3` 一类），离线推理、无 API 费用。Chroma 的 `embedding_function` 指向它。
- **Grep 检索策略**：混合 —— 默认「向量命中文件后定向 grep」，当向量得分全部偏低时兜底触发「全目录全量 grep」作为召回补充。
- **简历优化架构（重要调整，偏离设计文档原描述）**：简历优化逻辑**独立为一个子图 / 子 agent（`resume_agent`）**，不散落在主图 `chat_node` / Memory。主图 router 识别"优化简历"意图后激活该子 agent；草稿暂存 / 回退状态持久化到 Store（按 `user_id` 存版本栈），前端在激活时进入草稿维护态。**复杂逻辑全部下沉到子图，主图只做路由激活。** `MainState` 预留 `resume_subgraph_input` / `resume_subgraph_output` 槽位。

## 用户的要求（协作约束，硬性）

分阶段根据设计文档与计划进行开发。核心约束：

1. **先有总体计划草稿**：开工前先拟定一份开发计划草稿，用户无异议后才进入分阶段实现。
2. **每阶段实现前必须协商**：每个阶段动手写正式代码前，用「文字陈述 + 快速原型」与用户交流本阶段的实现边界和细节，**不断迭代直到所有开发细节清晰且双方无异议**，才请求用户批准开发。
3. **快速原型隔离**：快速原型统一放入项目专用文件夹 `prototypes/`，**不得污染** `src/`、`tests/` 等正式代码目录。原型是可运行的最小验证，用于降低关键技术风险，不是正式实现。
4. **测试用例编写准则**：不要纠结代码规范相关问题，测试用例应该精简，当发现编写测试用例的总是运行不起来，及时提问用户，是否需要这个测试，如果确实需要，那么和用户讨论编写测试用例时遇到了什么问题。
5. **阶段验收后才存档**：每个阶段完成且**用户验收通过后**，在用户指引下进行 git 存档（提交/打 tag）。未验收不存档，未指引不自行提交。
6. **分阶段闭环**：每阶段固定走 边界陈述 → 原型 → 协商 → 正式实现（满足 ruff + mypy `--strict` + 测试）→ 验收 → git 存档，不跳步。
7. **不跨阶段占位（硬性）**：开始某阶段时，**绝不为本阶段不需要、但后续阶段会用的文件/函数/模块/占位**提前创建。每阶段只产出本阶段实际需要的代码。后续阶段要用的东西，等到那个阶段再建——哪怕现在"顺手建个空文件"也不行。
8. **依赖按需安装**：开发环境无需提前装齐。编写代码用到某个包时，**提示用户安装**（给出包名与用途），不由 Claude 自行安装。

> 协作铁律：宁可多协商一轮，也不要在细节未对齐时写正式代码。用户没说"开始/批准"就不动手实现。
