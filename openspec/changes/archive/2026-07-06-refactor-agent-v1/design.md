## Context

项目是基于 LangGraph 的本地面试助手 MVP，已完成 Phase 0–6：主图（`chat_node` + `route_after_chat`）+ 四个子 agent（`rag_agent` / `resume_agent` / `research_agent` / `index_agent`）+ FastAPI `/v1/chat` SSE 端到端 + `SqliteSaver`/`SqliteStore` 持久化 + HITL 深研。功能验收通过，但横切关注点散落在各 agent 内部，已识别六类技术债（详见 CLAUDE.md「已知技术债」与 proposal.md）。

关键约束（来自 CLAUDE.md 与已确认决策）：
- **Studio 子图发现硬约束**：wrapper 必须"模块顶层 import 已编译子图 + 函数体裸名引用"才能被 `find_subgraph_pregel`（`langgraph/pregel/_utils.py`）静态发现，Studio 才能展开子图内部节点。惰性 getter / 体内 import / 体内编译均不可发现。已由 `prototypes/phase7_registry_studio_probe.py` 实证（6/6 PASS）。
- **协作约束**：分阶段闭环（边界陈述 → 协商 → 实现 → 验收 → 存档），不跨阶段占位，原型隔离在 `prototypes/`，依赖按需提示安装。
- **不改变已验收功能行为**：纯重构，`/v1/chat` 协议、子图拓扑、interrupt 交互模型保持不变。

## Goals / Non-Goals

**Goals:**
- 收敛六类横切关注点为唯一真相源（持久化路径、模型名、registry、Studio 发现、子图互依、import 风格）。
- 引入可拔插 registry，使新增子 agent 降至"一 wrapper 模块 + 清单一行"，且不破坏 Studio 子图展开。
- 修复 `research_agent` 的 Studio 子图发现（惰性 getter → 顶层 import）。
- 物理目录与方案 C 一致（kernel + agents/ 自包含子包）。
- 模型名配置化（选项 A：重启切换，非 per-request 注入）。
- interrupt payload 用 Pydantic 固化契约。

**Non-Goals:**
- 不做 per-request / per-user 注入式多模型切换（选项 B，留待真有需求）。
- 不迁移旧持久化数据（`state_db.sqlite` / `sqlite_store.db` 重置可接受）。
- 不改变任一子图的内部节点拓扑、state schema、prompt 内容。
- 不重写 `static/index.html`（临时前端），仅保证 interrupt payload 形状兼容。
- 不引入 `AgentRegistry` 动态注册表 + 结构化决策（设计文档原描述）——采用更轻的显式清单 registry。
- 不改变 Index Agent 后台定位（仍不进主图，`research_agent` 直接 ainvoke 它保留为已知技术债，本次不改走导入接口）。

## Decisions

### D1：采用方案 C「智能体自治 + 共享 kernel」，不做方案 A 全分层
**选择**：保留各 agent 自包含子包结构（`graph.py` / `state.py` / `tools/`），新增 `src/kernel/` 收横切，`src/agents/` 归集所有 agent。
**理由**：方案 C 精准命中"横切散落"根因，不推翻已贯彻 6 个 Phase 的"agent 自包含"约定；方案 A 迁移面过大、需重写 Studio 发现路径，风险/收益不划算；方案 B 违背 CLAUDE.md 子智能体目录硬性约定，排除。
**备选**：方案 A（Clean/Hexagonal 全分层）——否决，迁移成本与 Studio 适配风险高。

### D2：registry 为显式清单，不合成节点函数
**选择**：`registry.py` 持 `list[SubAgentMeta]`，每条含 `{name, tool, node, route_key}`，`node` 是**静态 wrapper 函数引用**（非工厂产物）。`build_main_graph` 遍历它做 `add_node`/`add_edge`/`path_map`/`ALL_TOOLS`；`route_after_chat` 查 `ROUTE_TABLE`（非 if 链）。
**理由**：Studio 的 `find_subgraph_pregel` 分析对象是节点函数本身的 AST，不是它被怎么注册的。只要 `add_node` 传的是模块级静态函数、其函数体裸名引用模块级已编译子图，registry 怎么组织都不影响发现。原型实证 REGISTRY 布线 + 静态 wrapper → subgraphs 全部命中。
**备选**：闭包工厂 `make_node(subgraph)`（原型 H3 也可被发现）——备案，但用户偏好静态 wrapper 连贯性，不采用。装饰器自动注册——否决，难 grep、加载顺序不可控。

### D3：所有 wrapper 统一为模块顶层 import 已编译子图
**选择**：三个 wrapper（rag/resume/research）模块顶层 `from <agent>.graph import graph as X_graph`，函数体 `await X_graph.ainvoke(...)`。删除 `research_agent` 的 `_get_research_graph()` 惰性模式。
**理由**：惰性 getter 返回值是函数非 Pregel，`find_subgraph_pregel` 无法发现（原型 H2 实证为空）。rag/resume 已是顶层 import 不退化；research 改为顶层 import 同时修复 Studio 发现。
**前置条件**：必须先拆循环依赖（见 D4），否则顶层 import 会触发 `research_agent.graph → kernel.logging → agents.main.__init__ → agents.main.graph → agents.main.tools.research_agent → research_agent.graph` 环。

### D4：拆循环依赖——横切上提 + `agents/main/__init__.py` 最小化
**选择**：① `agent.debug` → `kernel/logging.py`（零依赖）；② `src/client.py` → `kernel/llm.py`；③ `agents/main/__init__.py` 不再 eager `from agents.main.graph import graph`，改为最小化（仅暴露 `build_main_graph`，不触发整图加载）。`langgraph.json` 仍指向 `agents/main/graph.py:graph` 模块级实例，不受影响。
**理由**：环的根因是 `agent/__init__.py` eager import `graph`，而子图依赖 `agent.debug` 属于 `agent` 包触发 `__init__`。横切移出 `agent` 包 + `__init__` 最小化 = 断环。断环后三个 wrapper 可堂堂正正顶层 import，registry 才稳。

### D5：持久化收口到 `kernel/persistence.py`，Studio 与 server 共用路径常量
**选择**：`kernel/persistence.py` 提供 `get_checkpointer()`（async，供 server/Studio）+ `get_store()`（sync，供主图）+ 路径常量 `CHECKPOINT_DB_PATH` / `STORE_DB_PATH`。`checkpointer.py` 与 `server.py` 都引用同一常量。
**理由**：当前三套并存（`state_db.sqlite` 给 Studio、`sqlite_checkpoints.db` 给 server 但未生成、`sqlite_store.db` 给 Store），Studio 调的图状态与 server 跑的图状态互相看不见。统一路径常量 = Studio 与 server 同库，断点恢复/多用户隔离地基稳固。
**注**：旧数据不迁移，迁移期重置可接受（MVP 阶段）。

### D6：模型名收进 `kernel/config.py`，LLM 工厂读 config（选项 A）
**选择**：`kernel/config.py`（pydantic-settings 或纯环境变量读取）集中 `CHAT_MODEL` / `EXTRACTION_MODEL` / provider 配置；`kernel/llm.py` 的 `get_chat_model(model=None)` 默认读 config。各 agent 不再硬编码 `"deepseek-v4-flash"`。
**理由**：消除 5+ 处魔法字符串，改 config 即切模型（重启生效）。
**Non-Goal**：不拆全局单例 `_chat_model` 为 per-request 注入（选项 B），保留各 agent 模块级 LLM 缓存。

### D7：interrupt payload 用 Pydantic 固化进 `kernel/contracts.py`
**选择**：定义 `PlanConfirmPayload` / `StepConfirmPayload` / `OutlineConfirmPayload` / `ConnectivityCheckPayload` + `ResumeDecision`（`approve`/`reject`/`suggest`）schema。server 序列化、子图 `interrupt()` 构造、解析共用同一组类型。
**理由**：当前协议散在 server.py + 3 个子图 + 前端 JS，无类型约束。固化后改协议有编译期保障。前端 `static/index.html` 不重写，仅保证序列化后形状兼容。

### D8：物理目录结构（方案 C 最终树）
**选择**：
```
src/
  kernel/
    __init__.py
    config.py          # 模型名/provider/阈值 env 读取
    paths.py           # PROJECT_ROOT + data/markdown + data/chroma + data/index.md
    llm.py             # get_chat_model（从 src/client.py 迁入 + 读 config）
    embedder.py        # SiliconFlowEmbeddingFunction + COLLECTION_NAME 常量（rag/index 共享）
    persistence.py     # checkpointer + store 工厂 + 路径常量（唯一真相源）
    logging.py         # dlog/slog（从 agent/debug.py 迁入）
    contracts.py       # interrupt payload Pydantic schema
  agents/
    __init__.py        # 最小化，不 eager import graph
    main/
      __init__.py      # 最小化
      graph.py         # build_main_graph 遍历 REGISTRY 布线；模块级 graph
      state.py         # MainState
      routing.py       # route_after_chat（查 ROUTE_TABLE）
      registry.py      # SubAgentMeta + REGISTRY 显式清单 + ROUTE_TABLE
      prompts.py       # 主图 system prompt + 记忆提取 prompt
      nodes/
        chat.py        # chat_node
        memory.py      # save_memory_node（从 agent/memory.py 迁入）
      tools/
        rag_agent.py       # @tool + 静态 wrapper（顶层 import rag_graph）
        resume_agent.py    # @tool + 静态 wrapper（顶层 import resume_graph）
        research_agent.py  # @tool + 静态 wrapper（顶层 import research_graph）
    rag/               # graph.py / state.py / prompts.py / tools/
    resume/            # graph.py / state.py / prompts.py / tools/
    research/          # graph.py / state.py / prompts.py / tools/
    index/             # graph.py / state.py / tools/（后台 agent，外加 CLI 唤醒入口）
  server/
    __init__.py
    app.py             # FastAPI（从 agent/server.py 迁入）
  cli.py               # index_agent 主动唤醒等命令入口
```
**理由**：kernel 收横切、agents 自包含、server/cli 为适配层。与 CLAUDE.md 子智能体目录硬性约定一致（folder name = agent name）。

### D9：统一 import 风格——裸包名，项目根为工作目录
**选择**：消除所有 `from src.client import ...` 与 `from src.xxx`，统一为 `from kernel.llm import ...` / `from agents.rag.graph import ...`。`pyproject.toml` 包映射更新为 `kernel` / `agents` / `server`。
**理由**：`src` 不是声明包，`from src.client` 只在特定 PYTHONPATH 下能跑，打包成轮子即脆。裸包名从项目根可 import，可移植。

### D10：Prompt 与常量分层管理
**选择**：
- **Prompt 集中到各 agent 的 `prompts.py`**：每个 agent 包（含 main）建 `prompts.py`，导出该 agent 所有 prompt。纯文本 prompt 用字符串常量；含变量的（如 `PLAN_PROMPT`）封装成 `build_plan_prompt(resume, intent) -> str` 函数，避免 `.format()` 散落节点。节点函数 `from .prompts import ...`。
- **跨 agent 共享常量上提 kernel**：`COLLECTION_NAME`（`retrieval.py` 与 `vectorstore.py` 两处重复定义）→ `kernel/embedder.py` 唯一定义，rag/index 共享。
- **per-agent 领域调参常量留 agent 内**：RAG 阈值（`SCORE_LOW_THRESHOLD`/`GAP_RATIO_THRESHOLD`/`VECTOR_N_RESULTS`）留 `rag_agent/tools/retrieval.py` 顶部；research slug 规则留 `research_agent/`；resume section 正则留 `resume_agent/tools/crud.py`。不上提 kernel，避免 kernel 膨胀。
- **不外部化 prompt 到 YAML/JSON**：MVP 阶段 prompt 留代码，便于版本控制与 mypy。

**理由**：prompt 是高频调参项，集中后改 prompt 不碰节点逻辑；跨 agent 真重复的常量上提消除耦合；per-agent 专属常量按「自包含」原则留 agent 内。

## Risks / Trade-offs

- **[循环依赖未拆净导致顶层 import 失败]** → D4 必须在 D3 之前完成；R0 先建 kernel + 迁横切 + `__init__` 最小化，验证 `import agents.main.graph` 不触发环后再改 wrapper 顶层 import。
- **[物理重排牵动全量 import，遗漏导致 import error]** → R3 单独成阶段，重排后用 `langgraph dev` 起图 + 跑 tests/ 全量验证；分批迁移（先建 agents/ 软链，逐个 agent 切，最后删旧 src/agent）。
- **[registry 布线的图 Studio 仍展不开子图]** → 已由原型实证（REGISTRY 布线 + 静态 wrapper 全部命中）。R1 完成后用 Studio 实际打开 main_agent 验证三个子图均可展开。
- **[持久化路径统一后旧 thread 状态丢失]** → MVP 阶段可接受重置；若需保留，可在 R0 提供一次性迁移脚本（Non-Goal，按需再加）。
- **[Pydantic schema 与前端现有 payload 形状不一致]** → D7 的 schema 序列化后字段名/结构与现有 server 输出对齐，`static/index.html` 不改即可继续工作；R2 验收时跑一次端到端 HITL 流程确认。
- **[kernel 边界滑成上帝模块]** → 纪律约束：只放"真正跨 agent 共享"的横切；per-agent 的 prompts/state/tools 不进 kernel。

## Migration Plan

分四阶段（R0–R3），每阶段独立可验收，按 CLAUDE.md 走「边界陈述 → 协商 → 实现 → 验收 → 存档」闭环：
1. **R0 地基**：建 `kernel/`，迁横切（paths/config/llm/embedder/persistence/logging），`__init__` 最小化拆环，统一持久化路径。验收：`langgraph dev` 起来 + 主图端到端跑通 + Studio 仍展开 rag/resume。
2. **R1 registry**：引入 `registry.py`/`routing.py`，三 wrapper 改顶层 import（修 research Studio bug），main graph 遍历 REGISTRY 布线。验收：Studio 首次展开 research 子图 + SSE 端到端。
3. **R2 模型配置化 + interrupt 契约**：模型名进 config，interrupt payload 进 `kernel/contracts.py`。验收：改 config 切模型生效 + HITL 流程 schema 校验通过。
4. **R3 物理重排**：`src/agent/` → `src/agents/main/`，子 agent 归 `src/agents/`，更新 `langgraph.json`/`pyproject.toml`。验收：`langgraph dev` + tests/ 全绿。

**回滚**：每阶段独立 git 提交（验收后存档），任一阶段验收失败可回退到上一阶段提交。R3 物理重排前确保 R0–R2 已存档，重排失败可整体回退。

## Open Questions

- R0 持久化统一后，Studio 与 server 是否**完全共用同一 db 文件**（含并发写入）？当前设计为同一路径常量；若并发写入有 WAL 冲突，需在验收时观察。倾向：共用，SQLite WAL 模式可容忍。
- `kernel/config.py` 用 pydantic-settings 还是纯 `os.getenv`？倾向 pydantic-settings（类型安全 + 与 server 的 Pydantic 一致），但引入 `pydantic-settings` 依赖需提示用户安装。
- index.md 表格格式契约（引用语法 regex + 三列结构）目前 `retrieval.py` 与 `index_io.py` 各自独立实现（两处 regex 重复但形式一致）。本次**不上提**（轻量耦合，避免过度统一），留待日后格式变更频繁时再固化到 `kernel/index_format.py`。
