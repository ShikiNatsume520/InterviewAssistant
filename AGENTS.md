# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 用户的要求（协作约束，硬性）

分阶段根据设计文档与计划进行开发。核心约束：

1. **先有总体计划草稿**：开工前先拟定一份开发计划草稿，用户无异议后才进入分阶段实现。
2. **每阶段实现前必须协商**：每个阶段动手写正式代码前，用「文字陈述 + 快速原型」与用户交流本阶段的实现边界和细节，**不断迭代直到所有开发细节清晰且双方无异议**，才请求用户批准开发。
3. **快速原型隔离**：快速原型统一放入项目专用文件夹 `prototypes/`，**不得污染** `src/`、`tests/` 等正式代码目录。原型是可运行的最小验证，用于降低关键技术风险，不是正式实现。
4. **测试用例编写准则**：不要纠结代码规范相关问题，测试用例应该精简，当发现编写测试用例的总是运行不起来，及时提问用户，是否需要这个测试，如果确实需要，那么和用户讨论编写测试用例时遇到了什么问题。
5. **阶段验收后才存档**：每个阶段完成且**用户验收通过后**，在用户指引下进行 git 存档（提交/打 tag）。未验收不存档，未指引不自行提交。
6. **分阶段闭环**：每阶段固定走 边界陈述 → 原型 → 协商 → 正式实现（满足 ruff + mypy `--strict` + 测试）→ 验收 → git 存档，不跳步。
7. **不跨阶段占位（硬性）**：开始某阶段时，**绝不为本阶段不需要、但后续阶段会用的文件/函数/模块/占位**提前创建。每阶段只产出本阶段实际需要的代码。后续阶段要用的东西，等到那个阶段再建——哪怕现在"顺手建个空文件"也不行。
8. **依赖按需安装**：开发环境无需提前装齐。编写代码用到某个包时，**提示用户安装**（给出包名与用途），不由 Codex 自行安装。
9. **不跑拼写/规范性检查**：不要主动运行 codespell 等拼写检查或纯规范性校验——不重要且浪费 token。ruff（代码缺陷/import 顺序）与 mypy（类型）这类能抓真实问题的检查照常运行，功能正确性优先用 pytest 验证。

> 协作铁律：宁可多协商一轮，也不要在细节未对齐时写正式代码。用户没说"开始/批准"就不动手实现。

## 常用命令

**包管理一律使用 `uv`（仓库根有 `uv.lock`）**——这是本项目的环境约定，时刻记住不要混用 pip 安装依赖。`uv venv` 创建环境、`uv pip install -r pyproject.toml` 装依赖、`uv run <cmd>` 在项目环境内执行命令。

```bash
# 安装（含 LangGraph CLI，用于本地 server）
uv venv && uv pip install -r pyproject.toml

# 启动 LangGraph Server（热重载，配合 LangGraph Studio 可视化调试）
uv run langgraph dev

# 测试
uv run python -m pytest tests/unit_tests/                       # 单元测试
uv run python -m pytest tests/integration_tests                 # 集成测试（anyio + @pytest.mark.langsmith）
uv run python -m pytest tests/unit_tests/test_xxx.py            # 单个测试文件
make test TEST_FILE=tests/unit_tests/test_xxx.py                # 通过 Makefile 指定文件

# Lint / 格式化（CI 会强制执行，本地应先跑）
make lint          # ruff check . + ruff format --diff + ruff check --select I + mypy --strict（针对 src/）
make format        # ruff format + ruff check --select I --fix
make lint_package  # 仅检查 src/
make lint_tests    # 仅检查 tests/（独立 mypy cache）
codespell --toml pyproject.toml   # 拼写检查（CI 对 README.md 与 src/ 强制；本地不主动跑，见「用户的要求」第 9 条）
```

CI（[.github/workflows/unit-tests.yml](.github/workflows/unit-tests.yml)）在 Python 3.11/3.12 上运行 ruff + mypy `--strict` + codespell + pytest。

## 技术选型决策（已与用户拍板）

- **LLM provider**：OpenAI 兼容端点（`langchain-openai` 的 `ChatOpenAI`，`base_url` / `model` / `api_key` 走 `.env` 配置）。可对接 OpenAI / DeepSeek / Qwen / 智谱等任意 OpenAI 兼容服务。所有 `chat_node`、Memory、Deep Research 节点统一用此。
- **Embedding**：SiliconFlow API（`BAAI/bge-large-zh-v1.5`，1024 维，OpenAI 兼容端点，`SILICONFLOW_*` env 配置），在线推理。Chroma 的 `embedding_function` 指向 `src/agents/index/tools/vectorstore.py:SiliconFlowEmbeddingFunction`（单例缓存）。
- **检索策略**：`rag_agent` 提供两条独立检索管道（`semantic` 向量 / `keyword` grep），由 LLM 通过工具参数 `search_type` 显式选择。向量管道置信度不足（top/avg 分差比或绝对得分不达标）时直接判知识缺口（gap），**不做兜底**；关键词管道经 `index.md` 候选 → 定向 grep（无候选则全量 grep）。
- **简历优化架构（偏离设计文档原描述，已实现）**：简历优化独立为子图 `resume_agent`（计划-确认-执行循环），不散落在主图 `chat_node` / Memory。主图 `route_after_chat` 识别"优化简历"意图后激活该子 agent。草稿维护在 `ResumeState.current_draft` / `last_draft`（内存态 + checkpointer 持久化），CRUD 工具通过 `ToolRuntime` 读改 state、返回 `Command(update=...)`。**未持久化到 Store**（设计文档原提的"按 user_id 版本栈"未实现，属已知技术债）。主图与子图通过 `ToolMessage` 通信，无预留槽位。

## 开发约定

- **行级引用是硬约束**：知识库切片必须在 metadata 保留 `start_line` / `end_line`，检索结果必须附带文件名+行区间——这是设计文档反复强调的核心需求，实现 RAG 时不可省略。
- **LangGraph Studio 是首要调试工具**：`langgraph dev` 启动后可编辑历史 state、从任意节点重跑、查看子图内部变量。改图后热重载。
- **新增子智能体时**：遵循当前 registry + wrapper 模式——在 `src/agents/main/tools/<name>.py` 写 `@tool` 工具 + 异步 wrapper 节点（函数体**模块顶层 import 已编译子图实例并裸名引用**），并在 `src/agents/main/registry.py` 的 `REGISTRY` 清单登记一条。**Studio 子图发现硬约束**：wrapper 必须"模块顶层 `from <agent>.graph import graph as X` + 函数体 `await X.ainvoke(...)`"——惰性 getter / 体内 import / 体内编译均无法被 `find_subgraph_pregel` 静态发现，Studio 将展不开子图内部节点（详见 `prototypes/phase7_registry_studio_probe.py` 与 LangGraph 源码 `pregel/_utils.py`）。registry 仅作布线清单，不绕开此约束。
- **子智能体目录结构（硬性约定）**：每个子图 / 子 agent 的相关逻辑**全部集中到 `src/agents/<name>/` 下独属文件夹**，不散落到主图或公共模块。文件夹内大致结构：
  - `graph.py` — 该智能体的图逻辑（节点、边、编译）。
  - `state.py` — 该智能体需要的状态及数据结构定义（TypedDict / Pydantic 等）。
  - `tools/` — 该智能体可使用的工具（按需分子模块）。
  - 其余辅助模块（如 `prompts.py`、`retrieval.py`）按需新增，但务必**自包含**在本文件夹内。
  - 主图 `src/agents/main/graph.py` 仅通过 wrapper 节点 + `ToolMessage` 引用子图，不直接实现子图内部逻辑。
  - 命名：文件夹名即子 agent 名（`rag` / `resume` / `research` / `index`），与 `langgraph.json` 注册名一致。
  - **Index Agent 是例外**：它不进主图流程，是后台 agent（详见下文「Index Agent 定位」），但仍遵循本目录结构约定，放 `src/agents/index/`，外加一个可被导入接口 / CLI 唤醒的入口。
- **Index Agent 定位（后台 agent，不进主图）**：职责仅是「在导入新 Markdown 时整理更新 `data/index.md` + Chroma 向量灌入」。触发方式：① 前端就绪后，由**非对话的数据导入接口**自动唤醒；② 前端未实现时，提供**主动唤醒命令**（CLI / 脚本入口）手动激活处理一批文件。**主图不应直接调用 Index Agent**——主图只读 `data/index.md` 与 Chroma（经 `rag_agent` 检索工具）。注：`research_agent` 的 `index_rebuild_node` 现直接 `ainvoke` index_agent 图（跨子图调用），属已知技术债，待重构时改走导入接口/命令。
