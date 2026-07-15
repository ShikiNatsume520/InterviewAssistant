# 深研资料: OpenClaw 智能体（俗称龙虾）的记忆机制架构，包括其记忆分层设计、持久化方案、检索增强记忆等具体构成

## 记忆分层设计

- OpenClaw 默认记忆系统采用分层架构，核心层级包括：长期记忆层（`MEMORY.md`）、工作层（`memory/YYYY-MM-DD.md` 每日笔记）以及可选的梦境层（`DREAMS.md`）[1][4][7]。
- **长期记忆层（MEMORY.md）** 是紧凑、精选的持久存储，用于保存事实（facts）、偏好（preferences）和决策（decisions），在会话启动时加载。该层不包含原始记录或每日日志，内容会定期从每日笔记中提炼并更新，同时删除过时条目 [1][4]。
- **工作层（memory/YYYY-MM-DD.md）** 详细存储每日笔记、观察、会话摘要和原始上下文。今天和昨天的日期笔记在 `/new` 或 `/reset` 时自动加载。该层被 `memory_search` 和 `memory_get` 索引，但不会注入到每个轮次的 bootstrap prompt 中 [1][4]。
- **DREAMS.md（可选）** 用于存储梦境日记和 dream sweep 摘要，供人类审查 [1][4]。
- 部分高级架构（如 12 层记忆架构）将此基本分层扩展为包含连续记忆（Continuity）、稳定性（Stability）、代谢（Metabolism）和沉思（Contemplation）等插件，并在启动时从工作区文件（`MEMORY.md`, `USER.md`, `SOUL.md`, `AGENTS.md`）中自我重建 [2][10]。
- 另一分层模型将 OpenClaw 记忆描述为层次化向量系统，分为短时记忆（STM，持续数秒至数分钟）、中时记忆（MTM，跨数小时至数天）和长时记忆（LTM，跨数周至数年）。检索时按 STM → MTM → LTM 顺序使用近似最近邻（ANN）算法探查，并根据时效性和相关性加权 [12]。
- 从认知科学角度，OpenClaw 的记忆层次可映射为：**情景记忆**（每日日志，捕获具体经验）、**语义记忆**（MEMORY.md，抽象化精炼知识）和**程序记忆**（AGENTS.md/SOUL.md，可执行技能与行为模式）[13]。
- OpenViking 框架提出 L0/L1/L2 三层加载模型：L0 为始终加载的核心上下文，L1 为按需加载的目录级上下文，L2 为通过 VikingDB 向量搜索的深度检索 [6][11]。
- 每个 OpenClaw 代理维护自己独立的记忆孤岛，不同代理之间的记忆互不共享 [3]。

## 持久化方案

- OpenClaw 默认将所有记忆持久化为工作区（默认 `~/.openclaw/workspace`）中的纯 Markdown 文件，无隐藏状态。当 `MEMORY.md` 大小超过 bootstrap 文件预算时，磁盘文件保持完整，但在注入上下文时截断副本 [1][4][5]。
- 核心持久化文件结构：
  - `MEMORY.md`：存储持久的 facts、preferences 和 decisions [1][4]。
  - `memory/YYYY-MM-DD.md` 或 `memory/YYYY-MM-DD-<slug>.md`：存储运行上下文和观察 [1]。
  - `USER.md`、`SOUL.md`、`AGENTS.md`：存储用户信息、角色指令和行为约束，在启动时加载 [2][10][13]。
- 默认搜索索引使用 SQLite 数据库（`~/.openclaw/memory/{agentId}.sqlite`），存储文本内容、行范围和序列化嵌入向量，开箱即用支持关键词搜索、向量相似度和混合搜索 [1][4][9]。
- 除默认 SQLite 后端外，支持多种持久化后端：Local-first sidecar（带 reranking/query expansion）、AI-native 跨会话内存（带用户建模）、LanceDB（支持 OpenAI 兼容嵌入和本地 Ollama 嵌入）、QMD（本地优先搜索工具）[4][5][7]。
- 在 12 层架构中，持久化存储包括：`lcm.db`（消息、摘要、FTS 索引、DAG 节点）、`facts.db`（实体、关系、别名、衰减层级）、连续性归档（embeddings、topics、anchors）以及附加存储如 LightRAG PostgreSQL、llama.cpp 嵌入和每日 Markdown 文件 [2][10]。
- 嵌入提供商支持 OpenAI、Gemini、Voyage 或本地 `node-llama-cpp`，在 `config.json5` 中配置 [5]。
- 更改嵌入提供商或模型后，可手动删除 `~/.openclaw/memory/` 下的 SQLite 文件以强制重建索引 [5]。
- 通过 `@mem0/openclaw-mem0` 等插件可完全替换默认 Markdown+SQLite 系统，由 Mem0 处理提取、去重和语义搜索 [9]。

## 检索增强记忆（RAM）与工具

- **`memory_search`**：核心检索工具，在配置了 embedding 提供者后使用混合搜索：向量相似度（语义含义）结合关键词匹配（精确术语如 ID 和代码符号，通常为 70% 向量权重 + 30% BM25 权重）。由活跃记忆插件（默认 `memory-core`）提供 [1][4][7][9]。
- **`memory_get`**：读取特定记忆文件或行范围 [1][4][7]。
- 在 12 层架构中，`memory_search` 可并行运行四个后端：continuity（会话档案语义向量搜索，384d 嵌入）、facts（结构化实体/键值查找 + facts.db 的 FTS5）、files（工作区文档向量搜索 via file-vec index）、lcm（无损消息与摘要的全文搜索，lcm.db FTS5）。该统一搜索覆盖约 90% 的召回需求 [2][10]。
- 专用 LCM 工具处理剩余 10% 的精确查询：`lcm_grep`（正则/全文搜索）、`lcm_describe`（检查摘要元数据）、`lcm_expand_query`（生成子代理遍历 DAG，约 120 秒处理精确问题）[2][10]。
- **memory-wiki 插件**：将持久记忆编译成知识维基层，具有确定性页面结构、结构化声明与证据、矛盾与新鲜度追踪、生成仪表板和编译摘要，并提供 wiki 原生工具（`wiki_status`, `wiki_search`, `wiki_get`, `wiki_apply`, `wiki_lint`）[1][4][7]。
- 默认配置下，RAG 从长期记忆检索的“块”可能过小，导致 AI 将其视为语义噪声并忽略。可通过配置提示词调整 RAG 参数（如增大 chunk size）以提高检索准确度 [8]。
- R-Awareness 系统（ResonantOS Alpha）直接将完整基础文本注入上下文窗口，避免 RAG 分块导致的信息降级 [8]。
- 3 小时记忆日志协议通过 cron 作业强制 OpenClaw 按四个向量（What、Why/Intent、Mistakes、Fixes）总结操作日志并注入长期记忆，使 AI 能够检索精确错误修复及上下文成因 [8]。
- 行动敏感记忆需要捕获行动边界，包括批准/权限要求、临时约束、切换到其他会话/人、过期条件、安全执行时机、来源或所有者权限、避免行动的指令 [1][7]。
- OpenClaw 的批准设置、沙箱和计划任务提供硬操作控制，记忆仅保存批准上下文但不执行策略 [1]。

## 记忆管理：刷新、压缩与生命周期

- **自动记忆刷新（memory flush）**：在上下文压缩（compaction）前运行一个静默轮次，提醒 agent 将重要上下文保存到记忆文件。默认启用，可通过 `agents.defaults.compaction.memoryFlush.enabled: false` 关闭 [1][4][7]。
- **自动压缩（auto-compaction）**：当会话接近模型 token 限制时，旧的对话历史被总结为紧凑条目。压缩前会执行 memory flush [1][3][9]。
- agent 会定期从每日笔记中提炼有效内容到 `MEMORY.md`，并删除过时条目，该过程由工作区指令和心跳流程自动完成 [1]。
- **Commitments**：可选的短暂后续记忆，通过隐式后台扫描推断，范围限定在同一 agent 和 channel，通过 heartbeat 传递到期检查 [1]。
- 事实衰减系统：在 facts.db 中采用 Hot/Warm/Cool 三级衰减层级，通过 `superseded_at` 进行失效标记。事实写入器（Metabolism 插件）每 5 分钟运行一次 [2][10]。
- 记忆管理的“写入-管理-读取”循环中，“管理”环节（维护、修剪、压缩、合并）最常被忽视，是系统失败的主要原因 [13]。
- 常见陷阱：语义记忆缺乏策展会成为“杂物抽屉”；长上下文导致行为退化；为不同工作块创建新线程可避免单一线程变长后的性能下降 [13]。

## 插件生态与外部框架集成

- 核心记忆插件由 `openclaw.json` 的 `plugins.slots.memory` 配置，默认值为 `"memory-core"`。设置其他插件可完全替换默认系统 [9]。
- 记忆插件列表（部分）：
  - `memory-lancedb-pro`
  - `openclaw-supermemory`
  - `MemOS-Cloud`
  - `graph-memory`
  - `openclaw-memory-mem0`（替换 memory-core，由 Mem0 处理提取、去重和语义搜索）[6][9]。
- 可集成的外部记忆框架：Mem0、Zep、Letta、LangMem 等 [6]。
- mem9（ContextEngine-native 持久记忆）由 PingCAP/TiDB 创始人开发，是首个集成 TiDB Cloud 的 OpenClaw 记忆插件 [6]。
- OpenViking（字节跳动/火山引擎，17K+ 星）是开源 AI Agent 上下文数据库，使用文件系统范式，支持 L0/L1/L2 三层加载、自进化技能和视觉记忆追踪，后端依赖 VikingDB [6][11]。

## 高级架构与性能

- 12 层记忆架构（GitHub: coolmanns/openclaw-memory-architecture）：
  - Lossless Context Engine（LCM）：存储所有消息至 lcm.db，构建摘要 DAG，组装约 200K token 的上下文窗口 [2][10]。
  - 元认知管线（仅主 agent）：Metabolism → Gaps → Contemplation → Growth Vectors [2][10]。
  - 嵌入模型：nomic-embed-text-v2-moe（768d），支持 100+ 语言，GPU 延迟约 7ms。服务器运行于 llama.cpp Docker 容器（ROCm），零 API 成本 [2][10]。
  - 规模：770+ 事实/关系/别名（清理后），4,909 实体 / 6,089 关系（GraphRAG），使用 PostgreSQL + pgvector 和 OpenAI gpt-4.1-mini 提取 [2][10]。
  - 插件功能：Continuity（跨会话记忆、主题追踪）、Stability（熵监控、原则对齐）、Metabolism（基于 LLM 的事实提取、知识缺口检测）、Contemplation（三遍深度探究：explore → reflect → synthesize）[2][10]。
- 当 `MEMORY.md` 大小超过 bootstrap 预算时，磁盘文件完整但上下文注入副本被截断。可通过 `/context list`、`/context detail` 或 `openclaw doctor` 查看原始/注入大小和截断状态 [1]。
- 内存安全性威胁：包括 memory poisoning（记忆投毒）、intent drift（意图漂移）、privacy leakage（隐私泄漏）等 [6][13]。
- 记忆的“写入-管理-读取”循环中，“管理”环节（维护、修剪、压缩、合并）最常被忽视，是系统失败的主要原因 [13]。

## 来源

- https://docs.openclaw.ai/concepts/memory
- https://github.com/coolmanns/openclaw-memory-architecture
- https://memu.pro/blog/openclaw-memory-gap-memu-solution
- https://open-claw.bot/docs/concepts/memory/
- https://github.com/sologuy/Awesome-OpenClaw-Memory
- https://augmentedmind.substack.com/p/reclaiming-the-archive-the-openclaw-memory
- https://mem0.ai/blog/openclaw-memory-system-how-it-works-and-how-to-set-it-up
- https://ubos.tech/understanding-openclaws-memory-architecture-enabling-autonomous-ai-agents-3/
- https://towardsdatascience.com/a-practical-guide-to-memory-for-autonomous-llm-agents/