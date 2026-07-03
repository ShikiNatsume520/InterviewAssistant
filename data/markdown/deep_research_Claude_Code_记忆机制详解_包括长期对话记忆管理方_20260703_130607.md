# 深研资料: Claude Code 记忆机制详解：包括长期对话记忆管理方式、滑动窗口与摘要压缩策略、分层记忆架构、记忆持久化方案、检索增强记忆等具体设计细节

## 长期对话记忆管理方式

- MEMORY.md（约130行，200行硬上限）存储压缩的单行经验、近期会话摘要、架构决策、重复模式，以及指向主题文件的域映射路由表；第200行之后的内容会被静默截断，必须显式读取才能看到（[1]）。
- MEMORY.md 中的每条经验都带有时间戳，每周归档旧条目；同一经验出现在三个不同日期则自动升级为永久主题文件（[1]）。
- `~/CLAUDE.md` 存放全局指令、个性、会话生命周期规则，以及由 `<!-- BEGIN STATE -->` 和 `<!-- END STATE -->` 标记包围的 `## State` 区域，记录工作状态、阻塞项和下一步操作（[1]）。
- 每个项目目录下的 `CLAUDE.md` 在进入该目录时自动加载，用于项目级持久指令（[1][8]）。
- 会话命令 `/flush` 将状态同时持久化到三个位置：MEMORY.md、每日日志和项目 CLAUDE.md 的 State 区域（[1]）。
- 每日日志以 `~/llm-data/daily-log/YYYY-MM-DD.md` 格式存储，每次 `/flush` 追加时间戳条目，包含完成项、决策、活跃线程、经验和统计数据（文件数、读取数、主题）（[1]）。
- 自动记忆（auto memory）允许 Claude 从用户的纠正中学习，无需手动编写；子代理也可维护自己的自动记忆（[8]）。
- 用户可通过 Claude 界面查看、编辑或删除自己的记忆内容，记忆每 24 小时自动总结一次（[13]）。
- 记忆可暂停（保留现有记忆但停止新记忆创建）或重置（永久删除所有记忆，不可撤销）（[13]）。
- Claude 记忆的典型内容包括：角色、项目、专业背景、沟通/工作偏好、技术/编码风格、项目细节（[13]）。
- 单次会话中的错误会被系统在会话结束前转化为永久经验，防止后续会话重复出现（[1]）。

## 滑动窗口与摘要压缩策略

- Claude 聊天界面（如 claude.ai）使用滚动“先进先出”（FIFO）方式管理上下文窗口，即滑动窗口策略（[4]）。
- 对于长时间对话和 Agent 工作流，服务器端压缩（server-side compaction）是主要的上下文管理策略（[4]）。
- 所有输入输出（系统提示、消息、工具结果、图像、文档、思考块）均计入上下文窗口 token 计数（[4]）。
- 大多数 Claude 模型（如 Opus 4.8/4.7/4.6、Sonnet 5/4.6、Mythos 等）默认拥有 100 万 token 上下文窗口；其他模型如 Sonnet 4.5 拥有 200k token 窗口（[4]）。
- Claude Sonnet 5/4.6/4.5 和 Haiku 4.5 具备上下文感知能力，可自动追踪剩余 token 预算并使用 `<budget:token_budget>` 标签管理长时间任务（[4]）。
- 滑动窗口摘要策略：使用 `sliding_window_chunk(text, chunk_size, overlap)` 将长文档分割成重叠块，对每个块执行 map 步骤（LLM 生成摘要），然后 reduce 合并所有摘要；若合并后仍超窗口则递归处理（[5]）。
- 关键洞察：即使文档总长度在窗口内，分块摘要也比一次性完整摘要效果更好，可避免“lost in the middle”问题（[5]）。
- 当会话达到容量 70–80% 时推荐主动进行摘要转交：编辑最后一条用户消息，要求生成包含问题演变、关键见解、用户偏好等的“对话记忆日志”，然后将摘要复制到新对话继续（[6]）。
- 上下文漂移和上下文腐烂会在达到 token 限制前发生，Claude 性能在某个点后下降（[6]）。
- 替代方案：通过发布工件（artifact）获取新对话（保留工件清除历史），或使用“对话搜索”在新对话中引用旧对话（适合快速参考，不适合携带复杂状态）（[6]）。

## 分层记忆架构

- Claude Code 记忆是每次会话开始时注入到上下文的一组 Markdown 文件，文件因理解 Markdown 层次结构（标题/子标题、列表、粗体/斜体等）而被选用（[3]）。
- 三层记忆层次：全局（`~/CLAUDE.md`）、团队（可选的团队级 CLAUDE.md）、项目（项目目录下的 CLAUDE.md），每层作用于不同范围（[3][8]）。
- Claude Code 官方内存系统采用三层架构：第一层 MEMORY.md（充当其他知识的索引），第二层按需加载的主题文件（topic files），第三层可检索的完整会话转录（实现检索增强记忆）（[11]）。
- 项目级 CLAUDE.md 支持层次化放置：工作目录上方层级中的文件在启动时完整加载，子目录中的文件在读取该目录时按需加载（[8]）。
- `.claude/rules/` 目录可将指令限定到特定文件类型或子目录，实现路径作用域规则（[8]）。
- 自动记忆（auto memory）与用户编写的 CLAUDE.md 互为补充，子代理可独立维护自己的自动记忆（[8]）。
- 每个 CLAUDE.md 文件目标不超过 200 行，更短的文件消耗更少上下文并提高指令遵循度（[8]）。
- Claude Code 使用多代理架构，主代理通过 Task 工具将专业工作委托给子代理，子代理利用工作树（worktrees）实现隔离，子代理的并发状态通过 `claude agents` 视图实时显示（[2]）。
- 存在 “autoDream” 模式，用于“睡眠”期间合并记忆、去重、剪枝和消除矛盾（[11]）。

## 记忆持久化方案

- CLAUDE.md 文件是 Markdown 文件，放在不同位置（用户目录、项目根目录、项目根目录下 `.claude/`、`CLAUDE.local.md`）实现不同范围的持久化指令（[8]）。
- 记忆持久化通过 `/flush` 命令实现：同时更新 MEMORY.md、每日日志和项目 CLAUDE.md 的 State 区域（[1]）。
- 项目状态保存在项目 CLAUDE.md 的 `## State` 区域，包括工作状态、阻塞项、下一步操作及追加的近期日志，由 `/flush` 更新并由 cron 每周轮转（[1]）。
- memory-mcp 是一个开源 MCP 服务器，利用 CLAUDE.md 和 Claude Code 的钩子系统（Stop、PreCompact、SessionEnd）实现持久化记忆（[7]）。
- memory-mcp 的记忆分为两层：Tier 1 是 CLAUDE.md（约150行自动生成的简报），Tier 2 是 `.memory/state.json`（无限容量的完整记忆存储）（[7]）。
- 80% 的会话只需 Tier 1，其余 20% 通过 MCP 工具（`memory_search`、`memory_related`、`memory_ask`）按需访问 Tier 2（[7]）。
- 记忆类型包括：architecture、decision、pattern、gotcha（永久）、progress（7天衰减）、context（30天衰减）（[7]）。
- 去重机制：写时去重使用 Jaccard 相似度，若 token 重叠超过60%则新记忆取代旧记忆；LLM 合并每10次提取或活跃记忆超80时触发；置信度评分0-1，低于0.3的记忆排除出 CLAUDE.md 但仍可搜索（[7]）。
- CLAUDE.md 预算系统：每部分固定行数（architecture 25行、decisions 25、patterns 25、gotchas 20、progress 30、context 15），记忆按 `confidence * accessCount` 排序，未用预算重新分配，溢出时截断并提示使用 MCP 搜索工具（[7]）。
- 成本估算：每次提取约 $0.001，合并每10次约 $0.002，一天活跃开发 $0.05–0.10，无需向量数据库或嵌入 API（[7]）。
- `CLAUDE.md` 文件支持 `@path/to/import` 语法导入其他文件，相对路径相对于包含导入的文件解析，支持递归导入最多四层深度（[8]）。
- 可通过 `claudeMdExcludes` 配置在 monorepo 中跳过其他团队不相关的 CLAUDE.md 文件（[8]）。
- 使用文件为 Claude 创建跨所有聊天会话和项目的长期记忆：将业务上下文存储在文本文件中，每次任务只引用相关文件（[14]）。

## 检索增强记忆

- Claude Code 的三层记忆架构中，第三层是可检索的完整会话转录，实现检索增强记忆（retrieval augmented memory）（[11]）。
- 对于 Projects，当 project knowledge 接近上下文窗口限制时，Claude 自动启用 RAG 模式，将 project 容量提升至多达 10 倍（[9]）。
- RAG 模式下，Claude 使用 `project knowledge search tool` 从上传文档中检索最相关的信息，而非将所有内容加载到内存（[9]）。
- RAG 自动激活条件：project knowledge 接近或超过窗口限制；当 knowledge 低于阈值时自动切换回基于上下文的处理（[9]）。
- RAG 支持上传文档、图片及其他文件，可随时添加或移除内容，并支持引用特定文档（[9]）。
- RAG 适用于所有 Claude 计划（免费、Pro、Max、Team、Enterprise），无需用户配置（[9]）。
- RAG 保持与上下文处理一致的响应质量，与所有 Claude 工具（web search、extended thinking、Research）兼容（[9]）。
- memory-mcp 的 MCP 工具提供三种检索方式：`memory_search`（关键词搜索）、`memory_related`（按标签检索）、`memory_ask`（自然语言问题，由 Haiku 从 top30 记忆合成答案）（[7]）。
- Claude 平台的对话搜索（chat search）基于 RAG，可在所有聊天（除 project 外）中进行搜索，付费计划可用（Pro、Max、Team、Enterprise）（[13]）。
- 对话搜索可引用旧对话中的内容，但仅适合快速参考决策或方案，不适合携带复杂状态（[6]）。
- 记忆工具（Memory tool）允许 Claude 跨对话存储和检索信息，使用 `/memories` 目录下的文件，支持即时上下文检索（just-in-time context retrieval），适用于所有 Claude 4 及以上模型（[12]）。
- 记忆工具是客户端侧的：Claude 请求文件操作（创建、读取、更新、删除），由应用程序执行；路径限制在 `/memories` 内（[12]）。
- Python SDK 提供 `BetaLocalFilesystemMemoryTool` 类，可设置 `base_path` 参数（如 `base_path="./memory"`）（[12]）。

## 来源

- https://ianlpaterson.com/blog/claude-code-memory-architecture/
- https://deepwiki.com/anthropics/claude-code/1.1-system-architecture
- https://joseparreogarcia.substack.com/p/claude-code-memory-explained
- https://platform.claude.com/docs/en/build-with-claude/context-windows
- https://machinelearningplus.com/gen-ai/long-context-sliding-window-summarization/
- https://limitededitionjonathan.substack.com/p/ultimate-guide-fixing-claude-hit
- https://dev.to/suede/the-architecture-of-persistent-memory-for-claude-code-17d
- https://code.claude.com/docs/en/memory
- https://support.claude.com/en/articles/11473015-retrieval-augmented-generation-rag-for-projects
- https://www.askhandle.com/blog/explain-me-rag-in-simple-words
- https://news.smol.ai/issues/26-03-31-claude-code-leak
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool
- https://support.claude.com/en/articles/11817273-use-claude-s-chat-search-and-memory-to-build-on-previous-context
- https://www.producttalk.org/give-claude-code-a-memory/