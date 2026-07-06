# 项目进度
**进度**：Phase 0–6 全部完成并存档；R0–R3 系统性重构完成并存档（2026-07-06）。

- Phase 0 产出：`data/markdown/` 样例知识库、`prototypes/phase0_graph_probe.py`（LangGraph 链路探针）、CLAUDE.md、docs/开发计划.md、pyproject 的 prototypes ruff 忽略。`src/agent/graph.py` 仍为模板单节点占位（Phase 3 重构）。
- Phase 1 产出：`src/index_agent/`（后台 agent，五节点图 `scan→chunk→embed→llm_index→write_index`，不进主图、不进 AgentRegistry）。行级切片（chunk metadata 带 `start_line`/`end_line`）、Chroma 单 collection `knowledge_base` 向量灌入、LLM 维护文件级语义索引 `data/index.md`（指向文件不指行）。`langgraph.json` 注册了 `index_agent` 供 SDK/Studio 调试。SDK 验证脚本 `prototypes/phase1_sdk_test.py` 已跑通。
- Phase 2 产出：`src/rag_agent/`（双管道 RAG 子图，两节点 `retrieve→aggregate`）。Embedding 从本地 bge-small-zh 换为 **硅基流动 SiliconFlow API**（`BAAI/bge-large-zh-v1.5`，1024 维），Chroma 改用 **cosine 空间**。双管道（`semantic`：向量带分召回 + 标题节扩展合并 + 分差比 gap 判定 + 兜底全目录 grep；`keyword`：index.md 关键词匹配 + 定向 grep）。Gap 判据为 `top_score/avg_score < 1.15` 分差比。
- Phase 3 产出：`src/agent/` 动态主图总线（`chat_node` bind_tools + `route_after_chat`，现为简化版不采用 AgentRegistry + 结构化输出设计）。`rag_agent` 作为首个子智能体接入。
- Phase 4 产出：`src/agent/persistence.py`（SqliteSaver + SqliteStore 工厂）、`src/agent/memory.py`（长期记忆提取与持久化节点）。
- Phase 5 产出：`src/resume_agent/`（简历优化子图，计划-确认-执行循环，支持 interrupt 多轮人机交互）、`src/agent/server.py`（FastAPI `/v1/chat` SSE 端到端服务，含 interrupt 恢复）。
- **Phase 5.5 产出**：Studio 子图展开（模块级预编译 + wrapper 引用 + `find_subgraph_pregel` AST 闭包分析）、slog 双通道日志（get_stream_writer custom stream 进 Studio Server Logs，dlog 进终端）。
- **Phase 6 产出**：`src/research_agent/`（自主深研 HITL 子图，7 节点：outline → outline_confirm → connectivity_check → search → finalize → index_rebuild → abort）。ddgs 联网搜索 + httpx/trafilatura 爬取 + LLM 提炼笔记 + 写新 Markdown + 跨子图调 Index Agent 重建索引。主图接入：`ALL_TOOLS` 注册、`route_after_chat` 路由、system prompt 引导"先问用户再深研"。

目标系统定位：基于 LangGraph 的本地 MVP，做两件事——① Agent 领域知识学习问答（带本地知识库 RAG + 行级引用）；② 简历优化；③ 自主联网深研补足知识缺口。详见 [docs/开发计划.md](docs/开发计划.md)。

---

## 系统性重构 R0–R3（2026-07-06）

MVP 完成后的横切关注点收敛与模块化重排。四阶段闭环（每阶段：边界陈述 → 协商 → 实现 → 验收 → git 存档），openspec change `refactor-agent-v1` 已归档（`openspec/changes/2026-07-06-refactor-agent-v1/`）。

- **R0 地基**（commit `ae92087`）：新增 `src/kernel/`（paths/logging/llm/embedder/persistence）作为横切关注点唯一真相源；删 `src/client.py`、`agent/debug.py`、`agent/persistence.py`；import 统一为 `from kernel.*`（消除 `from src.client` 双重命名，修复 mypy 跑不起来）；持久化路径统一 `data/state/{checkpoints,store}.sqlite`（Studio 与 server 共用）；`agent/__init__.py` 最小化拆循环依赖；`COLLECTION_NAME` 上提 kernel 唯一定义；顺手修 11 个 R0 前 mypy 被掩盖的类型债。
- **R1 registry**（commit `66089fd`）：新增 `agents/main/registry.py`（`SubAgentMeta` + `REGISTRY` 显式清单 + `ROUTE_TABLE`）+ `routing.py`（查表路由，删 if-elif 链）；`research_agent` wrapper 从惰性 getter 改模块顶层 import，**修复 Studio 展不开 research 子图的潜伏 bug**（`subgraphs` 现非空）；`build_main_graph` 遍历 `REGISTRY` 布线。新增子 agent 降至「一 wrapper 模块 + 清单一行」。新增 [docs/agent-registration-guide.md](agent-registration-guide.md)。
- **R2 模型配置化 + interrupt 契约**（commit `e613019`）：新增 `kernel/config.py`（`DEFAULT_MODEL` + 4 场景常量 + provider，`os.getenv`）+ `kernel/contracts.py`（4 个 interrupt payload Pydantic schema + `SuggestDecision`）；`get_chat_model(model=None)` 读 config；8 处硬编码模型名消除；子图 interrupt 用 contracts schema 构造 + `model_dump()`；server `_interrupt_payload` 用 `_PHASE_TO_SCHEMA` 校验序列化。前端 payload 字段名兼容。**选项 A**（重启切换，不做 per-request 注入）。
- **R3 物理重排**（commit `e613019`）：`src/agent/` → `src/agents/main/`（含 `nodes/` + `tools/` 子目录），四子 agent → `src/agents/{rag,resume,research,index}/`（git mv 保留历史）；`src/agent/server.py` → `src/server/app.py`；新增 `src/cli.py`（index_agent 唤醒骨架）；7 个 prompt 抽到各 agent `prompts.py`（含变量的封装 `build_*()` 函数，节点无内联 prompt）；import 全量更新为裸包名；`langgraph.json` + `pyproject.toml` 包映射更新（kernel/agents/server）。

**最终目录树**（方案 C）：
```
src/
  kernel/   { config, contracts, paths, llm, embedder, persistence, logging }
  agents/
    main/   { graph, state, registry, routing, prompts, checkpointer, nodes/, tools/ }
    rag/    { graph, state, tools/ }
    resume/ { graph, state, prompts, tools/ }
    research/{ graph, state, prompts, tools/ }
    index/  { graph, state, tools/ }
  server/   { app }
  cli.py
```

**解决的 6 类技术债**：①持久化三套并存 → 统一；②模型名硬编码 5+ 处 → config 化；③加子 agent 改 4 处 → registry 一行；④research Studio 展不开 → 修复；⑤子图互依（rag→index 内部）→ embedder 上提 kernel；⑥import 混用 `src.client` → 统一裸包名。全程 ruff + mypy `--strict`（51 files）+ 子图发现不退化 + 端到端跑通。