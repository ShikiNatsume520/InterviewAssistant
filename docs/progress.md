# 项目进度
**进度**：Phase 0（骨架与前置）、Phase 1（Index Agent 与本地知识库基础设施）、Phase 2（混合 RAG 子图）均已完成并存档。当前进入 Phase 3。

- Phase 0 产出：`data/markdown/` 样例知识库、`prototypes/phase0_graph_probe.py`（LangGraph 链路探针）、CLAUDE.md、docs/开发计划.md、pyproject 的 prototypes ruff 忽略。`src/agent/graph.py` 仍为模板单节点占位（Phase 3 重构）。
- Phase 1 产出：`src/index_agent/`（后台 agent，五节点图 `scan→chunk→embed→llm_index→write_index`，不进主图、不进 AgentRegistry）。行级切片（chunk metadata 带 `start_line`/`end_line`）、Chroma 单 collection `knowledge_base` 向量灌入、LLM 维护文件级语义索引 `data/index.md`（指向文件不指行）。LLM 调用方式：`bind_tools([IndexUpdate])` + auto tool_choice（DeepSeek thinking 模型不能强制 tool_choice，后端取 `tool_calls` 校验 + 未调用兜底抽 JSON）。`langgraph.json` 注册了 `index_agent` 供 SDK/Studio 调试。SDK 验证脚本 `prototypes/phase1_sdk_test.py`（仿 `ref/sdk_test_ref.py`）已跑通。
- Phase 2 产出：`src/rag_agent/`（双管道 RAG 子图，两节点 `retrieve→aggregate`）。Embedding 从本地 bge-small-zh 换为 **硅基流动 SiliconFlow API**（`BAAI/bge-large-zh-v1.5`，1024 维），Chroma 改用 **cosine 空间**。双管道（`semantic`：向量带分召回 + 标题节扩展合并 + 分差比 gap 判定 + 兜底全目录 grep；`keyword`：index.md 关键词匹配 + 定向 grep）。Gap 判据为 `top_score/avg_score < 1.15` 分差比（余弦空间下比绝对阈值更鲁棒）。`langgraph.json` 注册了 `rag_agent` 供 SDK/Studio 调试。SDK 验证脚本 `prototypes/phase2_sdk_test.py` 已跑通，三场景验证通过（语义检索、关键词检索、gap 缺口）。
- **未落地**：[docs/设计文档.md](docs/设计文档.md) 的多智能体主图架构（V3）尚未实现，Phase 3 起逐步落地。不要把模板 `src/agent/graph.py` 当成已实现系统。

目标系统定位：基于 LangGraph 的本地 MVP，做两件事——① Agent 领域知识学习问答（带本地知识库 RAG + 行级引用）；② 简历优化。详见设计文档第 1 节。

各 Phase 的完整范围/验收标准见 [docs/开发计划.md](docs/开发计划.md)。