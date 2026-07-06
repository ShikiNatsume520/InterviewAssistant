## ADDED Requirements

### Requirement: 统一目录结构与 import 风格
系统 SHALL 采用方案 C 目录结构：`src/kernel/` 收横切关注点，`src/agents/<name>/` 归集各 agent（folder name = agent name），`src/server/` 放 FastAPI 适配层，`src/cli.py` 放命令入口。所有 import SHALL 以项目根为工作目录使用裸包名（`from kernel.llm import ...` / `from agents.rag.graph import ...`），SHALL NOT 使用 `from src.client import ...` 这类以 `src` 为前缀的 import。

#### Scenario: 无 src 前缀 import
- **WHEN** 在 `src/` 下搜索 `from src.` 或 `import src.`
- **THEN** SHALL 无匹配

#### Scenario: 包映射与目录一致
- **WHEN** 检查 `pyproject.toml` 的 `[tool.setuptools]` 包映射
- **THEN** 它 SHALL 声明 `kernel` / `agents` / `server` 等包并指向 `src/<name>`，SHALL NOT 保留 `src.client` 这类旧映射

### Requirement: 子智能体目录自包含
每个子 agent 包 SHALL 自包含其 `graph.py` / `state.py` / `tools/`，与 CLAUDE.md 子智能体目录硬性约定一致。主图 `agents/main/graph.py` SHALL 仅通过 wrapper 节点 + `ToolMessage` 引用子图，SHALL NOT 直接实现子图内部逻辑。

#### Scenario: 子 agent 包结构完整
- **WHEN** 检查 `src/agents/rag/`、`src/agents/resume/`、`src/agents/research/`、`src/agents/index/`
- **THEN** 每个 SHALL 含 `graph.py` 与 `state.py`，按需含 `tools/` 子目录，内部逻辑自包含

### Requirement: Prompt 集中管理
每个 agent 包（含 `agents/main/`）SHALL 含 `prompts.py`，集中管理该 agent 的所有 prompt。节点函数 SHALL 从 `prompts.py` import prompt，SHALL NOT 在节点函数或 `graph.py` 中内联 prompt 字符串常量。含变量的 prompt SHALL 封装为 `build_*()` 函数而非裸 `.format()` 调用。

#### Scenario: 节点不内联 prompt
- **WHEN** 检查任一 agent 的 `graph.py` 或节点函数
- **THEN** SHALL 不出现模块级 prompt 字符串常量（如 `PLAN_PROMPT = """..."""`），所有 prompt SHALL 在 `prompts.py` 中定义

#### Scenario: 各 agent prompts.py 存在
- **WHEN** 检查 `src/agents/main/prompts.py`、`src/agents/resume/prompts.py`、`src/agents/research/prompts.py`
- **THEN** 它们 SHALL 存在，分别集中该 agent 的 prompt（main 含 system + memory extraction；resume 含 plan + react；research 含 outline + distill + finalize）

### Requirement: langgraph.json 图发现路径更新
`langgraph.json` SHALL 指向重排后的图模块路径（`src/agents/main/graph.py:graph` 及 `src/agents/<name>/graph.py:graph`），与实际目录结构一致。

#### Scenario: langgraph dev 发现全部图
- **WHEN** 执行 `langgraph dev`
- **THEN** 它 SHALL 成功加载 `main_agent`、`rag_agent`、`resume_agent`、`research_agent`、`index_agent` 五个图，无 import 错误

### Requirement: index_agent 保持后台定位
`index_agent` SHALL 保持后台 agent 定位——不进主图流程、不在主图 registry 中注册。它 SHALL 提供可被 CLI / 导入接口唤醒的入口。

#### Scenario: index_agent 不在主图 registry
- **WHEN** 检查 `agents/main/registry.py` 的 `REGISTRY` 列表
- **THEN** 它 SHALL 不含 `index_agent` 条目
