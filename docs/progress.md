# 项目进度
**进度**：Phase 0–6 全部完成并存档。

- Phase 0 产出：`data/markdown/` 样例知识库、`prototypes/phase0_graph_probe.py`（LangGraph 链路探针）、CLAUDE.md、docs/开发计划.md、pyproject 的 prototypes ruff 忽略。`src/agent/graph.py` 仍为模板单节点占位（Phase 3 重构）。
- Phase 1 产出：`src/index_agent/`（后台 agent，五节点图 `scan→chunk→embed→llm_index→write_index`，不进主图、不进 AgentRegistry）。行级切片（chunk metadata 带 `start_line`/`end_line`）、Chroma 单 collection `knowledge_base` 向量灌入、LLM 维护文件级语义索引 `data/index.md`（指向文件不指行）。`langgraph.json` 注册了 `index_agent` 供 SDK/Studio 调试。SDK 验证脚本 `prototypes/phase1_sdk_test.py` 已跑通。
- Phase 2 产出：`src/rag_agent/`（双管道 RAG 子图，两节点 `retrieve→aggregate`）。Embedding 从本地 bge-small-zh 换为 **硅基流动 SiliconFlow API**（`BAAI/bge-large-zh-v1.5`，1024 维），Chroma 改用 **cosine 空间**。双管道（`semantic`：向量带分召回 + 标题节扩展合并 + 分差比 gap 判定 + 兜底全目录 grep；`keyword`：index.md 关键词匹配 + 定向 grep）。Gap 判据为 `top_score/avg_score < 1.15` 分差比。
- Phase 3 产出：`src/agent/` 动态主图总线（`chat_node` bind_tools + `route_after_chat`，现为简化版不采用 AgentRegistry + 结构化输出设计）。`rag_agent` 作为首个子智能体接入。
- Phase 4 产出：`src/agent/persistence.py`（SqliteSaver + SqliteStore 工厂）、`src/agent/memory.py`（长期记忆提取与持久化节点）。
- Phase 5 产出：`src/resume_agent/`（简历优化子图，计划-确认-执行循环，支持 interrupt 多轮人机交互）、`src/agent/server.py`（FastAPI `/v1/chat` SSE 端到端服务，含 interrupt 恢复）。
- **Phase 5.5 产出**：Studio 子图展开（模块级预编译 + wrapper 引用 + `find_subgraph_pregel` AST 闭包分析）、slog 双通道日志（get_stream_writer custom stream 进 Studio Server Logs，dlog 进终端）。
- **Phase 6 产出**：`src/research_agent/`（自主深研 HITL 子图，7 节点：outline → outline_confirm → connectivity_check → search → finalize → index_rebuild → abort）。ddgs 联网搜索 + httpx/trafilatura 爬取 + LLM 提炼笔记 + 写新 Markdown + 跨子图调 Index Agent 重建索引。主图接入：`ALL_TOOLS` 注册、`route_after_chat` 路由、system prompt 引导"先问用户再深研"。

目标系统定位：基于 LangGraph 的本地 MVP，做两件事——① Agent 领域知识学习问答（带本地知识库 RAG + 行级引用）；② 简历优化；③ 自主联网深研补足知识缺口。详见 [docs/开发计划.md](docs/开发计划.md)。