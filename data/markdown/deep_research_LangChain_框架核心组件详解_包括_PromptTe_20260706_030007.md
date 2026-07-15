# 深研资料: LangChain 框架核心组件详解：包括 PromptTemplate、Chain / LCEL（LangChain Expression Language）、Retriever、Agent 的 Tool Calling 机制、Memory 记忆管理、与 LangGraph 的集成使用方式、常见最佳实践与坑点

## PromptTemplate 高级用法与模板格式

- PromptTemplate 支持三种模板格式：f-strings（默认）、jinja2、mustache，通过 `template_format` 参数指定 [1]。
- 使用 `template_format='jinja2'` 时，需确保模板来自可信来源，否则可能导致任意 Python 代码执行。LangChain 0.0.329 起默认使用 `SandboxedEnvironment`，但仅作为 best-effort 安全措施 [1]。
- 推荐使用 `from_template` 类方法实例化 PromptTemplate：`PromptTemplate.from_template("Say {foo}")` [1]。
- 实例化后使用 `.format(foo="bar")` 方法填充模板变量 [1]。
- 也可直接使用初始化器：`PromptTemplate(template="Say {foo}")` [1]。
- PromptTemplate 可通过 `.input_variables` 属性获取所需输入变量名称列表，通过 `.partial_variables` 属性获取部分变量字典，通过 `.input_types` 属性获取期望的输入变量类型字典 [1]。
- `.format()` 方法格式化模板并返回字符串（PromptValue）；`.pretty_print()` 方法打印美观格式的提示 [1]。
- 支持配置字段：`.configurable_fields()` 列出可配置的 Runnable 字段；支持追踪元数据：`.metadata` 和 `.tags` 属性用于 tracing [1]。
- F-string 模板使用单花括号 `{variable}`，变量名必须为字母数字和下划线；不支持点号、括号、格式化说明符或表达式（如 `{price:.2f}`、`{x + y}`）[2]。
- 要在 f-string 输出中包含字面花括号，需双写括号 `{{` 进行转义 [2]。
- Mustache 语法使用双花括号 `{{variable}}`，支持通过点号访问嵌套对象（如 `{{user.profile.email}}`）[2]。
- Mustache 支持通过 `{{#name}}...{{/name}}` 迭代数组、条件渲染；通过 `{{^name}}` 反转部分处理空值状态；通过 `{{!---- ... ----}}` 添加注释 [2]。
- LangSmith 能自动在 f-string 和 mustache 模板间转换，但 mustache 的循环和条件功能无法转换回 f-string 格式 [2]。
- 使用 PromptTemplate 时必须确保 placeholder 名称与 `input_variables` 定义完全匹配，否则会引发运行时错误 [3]。
- `ChatPromptTemplate.from_messages()` 方法接受包含角色标识和模板字符串的元组列表，每个角色可设置独立动态变量 [3]。
- `MessagesPlaceholder` 类通过 `variable_name` 参数动态插入完整对话历史，适用于需要上下文记忆的聊天机器人 [3]。
- `partial()` 方法可预定义部分变量，例如 `base_template.partial(role="marketing expert")` 创建专用模板变体，减少冗余 [3]。

## Chain 与 LCEL（LangChain Expression Language）

- LCEL 使用管道操作符 `|` 链接组件，如 `prompt | llm | parser` 构成顺序链 [5][6]。
- 最小顺序链示例：`joke_chain = prompt | llm | parser`，其中 prompt 为 PromptTemplate，llm 为 HuggingFacePipeline，parser 为 StrOutputParser [5]。
- LCEL 自动类型强制转换：函数自动成为 transform runnable，字典自动成为 `RunnableParallel` 并行复合体 [5]。
- 并行扇出通过将任务放入字典实现，例如 `{'summary': ..., 'translation': ..., 'sentiment': ...}` 自动变为并行执行 [5]。
- LCEL 链需要正确安装依赖：`langchain>=0.2.0`、`langchain-huggingface>=0.0.3`、transformers、accelerate、torch、safetensors [5]。
- 模型加载时需设置 `device_map="auto"`（若 GPU 可用），否则设为 None；`torch_dtype` 设为 `torch.float16`（若 GPU 可用），否则 `torch.float32` [5]。
- `RunnableParallel` 对象可并行执行多个操作，例如 `RunnableParallel({"context": retriever_a, "question": RunnablePassthrough()})` [6]。
- `RunnablePassthrough` 对象将输入原样传递到输出字典的指定键（如 "question"）[6]。
- 使用多个并行信息流时，必须在 prompt 模板中为每个检索器指定对应上下文键（如 `{context_a}` 和 `{context_b}`）[6]。
- 链中只包含部分检索器（如仅使用 `vecstore_a` 的 `retriever_a`）可能导致信息缺失，无法正确回答问题，需确保所有必要上下文被整合 [6]。
- LCEL 支持异步、流式、追踪和扇出（fan-out）等运营特性 [5]。
- LCEL 链由组件通过 `|` 连接，Python 内部通过 `__or__` 方法实现链式调用 [6]。

## Retriever 在 LCEL 中的应用

- 在 LCEL 链中，检索器可以作为 `RunnableParallel` 的一部分被并行调用，例如 `RunnableParallel({"context": retriever_a, "question": RunnablePassthrough()})` [6]。
- 当使用多个检索器时，必须在 prompt 模板中为每个检索器指定对应的上下文键（如 `{context_a}` 和 `{context_b}`），否则链可能因缺少必要上下文而无法正确回答问题 [6]。
- 如果链中只包含了部分检索器，相关的上下文信息将不会传递给模型，导致回答不准确或失败 [6]。

## Agent 的 Tool Calling 机制与错误处理

- `AssembledToolCall` 对象包含 `status` 字段，取值可为 "running"、"finished"、"error"；`error` 字段为 string | undefined，在 status 为 "error" 时提供错误信息 [7]。
- 当 `toolCall.status` 为 "error" 时，UI 中的 `ToolCard` 组件会渲染 `ErrorCard` 组件，并传入 `name` 和 `error` 属性，显示标题 "Error in {name}" [7]。
- `useStream` 返回的 `toolCalls` 数组中的每个 `AssembledToolCall` 对象，其 `status` 可从 "running" 过渡到 "finished" 或 "error"，实现原地更新 [7]。
- 多个 tool call 可同时处于 "running" 状态，每个独立解析，UI 需通过过滤 `toolCalls` 分别处理 pending 和 completed 列表 [7]。
- `ToolNode` 类继承 `RunnableCallable`，通过 `handleToolErrors` 选项控制工具执行错误 [8]。
- 默认错误处理器 `defaultHandleToolErrors` 将工具调用错误（如 LLM 传入无效参数）转换为 `ToolMessage` 对象，使模型可查看错误并尝试重试 [8]。
- 满足 `isGraphInterrupt` 的错误（用于人机交互模式）会被重新抛出，以允许图暂停执行 [8]。
- `ToolInvocationError` 在工具参数未通过模式验证或使用无效参数时抛出 [8]。
- 当 `toolBehaviorVersion` 设为 "v2" 时，使用 LangGraph 的 `Send` API 并行执行工具调用，每个 `Send` 将工具调用放入 `lg_tool_call` 属性 [8]。
- 中间件通过 `wrapToolCall` 钩子包裹工具执行，每个中间件接收包含工具调用、工具实例、agent 状态和运行时上下文的 `ToolCallRequest` 对象 [8]。
- 工具可返回 `Command` 对象以控制 agent 流程，`ToolNode` 和 `AgentNode` 使用 `isCommand` 检测该对象 [8]。
- 工具配置 `returnDirect: true` 后，执行该工具时 agent 跳过返回模型，直接移至结束或下一工作流阶段 [8]。
- 可通过 LangChain agent 中间件处理工具错误，以重试失败的工具调用或返回自定义错误消息 [9]。

## Memory 记忆管理类型与实践

- **ConversationBufferMemory**：存储整个对话历史且无修改，适用于需要精确上下文的聊天机器人。导入：`from langchain.memory import ConversationBufferMemory`，创建实例：`memory = ConversationBufferMemory()` [11]。
- **ConversationBufferWindowMemory**：将记忆限制为固定窗口的最近会话，通过参数 `k` 控制窗口大小（如 `k=1` 只记住最近一次交换）。导入：`from langchain.memory import ConversationBufferWindowMemory`，创建：`memory = ConversationBufferWindowMemory(k=1)` [11]。
- **ConversationTokenBufferMemory**：基于 token 数量限制对话存储，用于控制成本和保持上下文，通过参数 `max_token_limit` 设置最大 token 数。导入：`from langchain.memory import ConversationTokenBufferMemory`，创建：`memory = ConversationTokenBufferMemory(llm=llm, max_token_limit=100)` [11]。
- **ConversationSummaryBufferMemory**：用于总结对话细节以维持在指定 token 限制内的上下文 [11]。
- **ConversationEntityMemory**：用于实体记忆。导入：`from langchain.memory import ConversationEntityMemory` 和 `from langchain.memory.prompt import ENTITY_MEMORY_CONVERSATION_TEMPLATE` [11]。
- LangChain 提供六种不同的记忆类型，每种针对不同的对话模式和 token 预算优化 [12]：
  - **Buffer/Window Memory**：适用于短对话（<10轮，仅需立即上下文），设置简单，无需数据库，便于调试完整对话历史。
  - **Summary Memory**：适用于长对话（50+轮）超出 token 限制、可进行上下文压缩、或需要跨会话连续性（如治疗、咨询）的场景。
  - **Vector Memory**：适用于非常长的历史记录（数月/年的交互），需通过语义搜索检索相关过往交流，支持跨会话个性化及 RAG+对话混合设置。
  - **Entity Memory**：适用于 CRM 集成（追踪客户姓名、公司、偏好）、随时间构建知识图谱、以及仅检索相关实体上下文的场景。
- 使用这些内存类型时，通常结合 `ConversationChain` 实例化，如 `conversation = ConversationChain(llm=llm, memory=memory, verbose=True)` [11]。
- LangChain Memory 的诞生是因为每个 LLM 调用默认完全无状态，不记得之前的消息 [13]。
- LangChain Memory 分为短期记忆和长期记忆，涵盖 Buffer、Window、Summary、Vector 等类型 [13]。

## 与 LangGraph 的集成使用方式

- LangChain agents 基于 LangGraph 构建，利用其持久执行（durable execution）、人工介入支持（human-in-the-loop）、持久化（persistence）等特性 [4]。
- `create_agent` 提供最小化、高度可配置的框架，从原始组件开始精确组合用例所需的功能，通过中间件增量添加能力，组合护栏、重试、路由和自定义工具策略 [4]。
- LangGraph 的内置内存存储对话历史并随时间维护上下文，支持有状态的跨会话交互 [10]。
- LangGraph 的人机交互检查（human-in-the-loop）允许引导和批准 agent 行动，直接影响状态管理 [10]。
- LangGraph 的低级原语支持自定义有状态工作流，包括单智能体、多智能体或层次化控制流 [10]。
- LangGraph 专为流式工作流设计，无代码开销，支持实时状态更新 [10]。
- LangGraph 是 MIT 许可的开源库，可免费使用 [10]。
- LangSmith 提供对每个 agent 决策（包括状态转换）的调试和评估 [10]。
- 使用 LangSmith 调试：在单一界面检查跟踪、工具调用、状态转换和延迟，以发现故障模式并优化 agent 行为 [4]。

## 常见最佳实践与坑点

- 使用 `jinja2` 模板格式时，务必确保模板来自可信来源，否则可能导致任意 Python 代码执行风险 [1]。
- F-string 变量名不能包含点号、括号或特殊字符（如 `{user.name}` 无效），需注意这点以避免运行时错误 [2]。
- Mustache 的循环、条件渲染等高级特性无法转换回 f-string 格式 [2]。
- 使用 `PromptTemplate` 时，确保 placeholder 名称与 `input_variables` 定义完全匹配，否则会引发运行时错误 [3]。
- 在 LCEL 链中整合多个检索器时，必须为每个检索器在 prompt 模板中指定对应的上下文键，以提供完整的上下文信息，避免信息缺失导致错误回答 [6]。
- 使用 `HuggingFacePipeline` 时，优先从 `langchain_huggingface` 导入，失败时回退到 `langchain_community.llms` [5]。
- 类型强制转换示例：函数 `enrich(d: dict)` 自动转换为可运行组件，但需注意函数内修改字典（如 `d["topic"] = d["topic"].lower`）可能不返回值 [5]。
- 工具调用错误处理中，默认错误处理器会将错误转换为 `ToolMessage`，使模型有机会重试；但满足 `isGraphInterrupt` 的错误会被重新抛出以允许图暂停 [8]。
- 工具配置 `returnDirect: true` 后，agent 会跳过返回模型，直接移至结束或下一工作流阶段，需小心使用以免打断正常 agent 流程 [8]。

## 来源

- https://reference.langchain.com/python/langchain-core/prompts/prompt/PromptTemplate
- https://docs.langchain.com/langsmith/prompt-template-format
- https://latenode.com/blog/ai-frameworks-technical-infrastructure/langchain-setup-tools-agents-memory/langchain-prompt-templates-complete-guide-with-examples
- https://python.langchain.com/docs/concepts/lcel/
- https://quantbender.github.io/posts/RAG-AND-AGENTIC-AI/8.langchain-expression-language/index.html
- https://www.pinecone.io/learn/series/langchain/langchain-expression-language/
- https://docs.langchain.com/oss/python/langchain/frontend/tool-calling
- https://deepwiki.com/langchain-ai/langchainjs/3.5-tool-execution-and-error-handling
- https://docs.langchain.com/oss/javascript/langchain/tools
- https://www.langchain.com/langgraph
- https://www.linkedin.com/pulse/langchain-memory-management-rutam-bhagat-lfrgf
- https://brlikhon.engineer/blog/building-ai-agents-with-long-term-memory-memu-vs-langchain-memory-complete-architecture-guide-
- https://javascript.plainenglish.io/langchain-memory-types-short-term-vs-long-term-memory-a-beginners-guide-a8b3dee847b4