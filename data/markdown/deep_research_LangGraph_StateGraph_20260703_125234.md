# 深研资料: LangGraph StateGraph

## 核心概念与基本结构

- LangGraph 是一个执行多步骤 Agent 或工作流的运行时，核心特性包括状态管理、分支、持久化。 [1]
- StateGraph 是 LangGraph 的构建器，包含四个原语：State（状态）、Nodes（节点）、Edges（边）、Compilation（编译）。 [1]
- 在 TypeScript 中，通过 `import { StateGraph, START, END, MessagesAnnotation } from "@langchain/langgraph"` 导入。 [1]
- 最简单的图构建流程：`new StateGraph(MessagesAnnotation).addNode("agent", async (state) => { ... }).addEdge(START, "agent").addEdge("agent", END).compile()`。 [1]
- StateGraph 是一个构建器类，不能直接用于执行，必须先调用 `.compile()` 生成 `CompiledStateGraph`。 [12]
- 节点函数的签名必须是 `State -> Partial<State>`，即接收完整状态，并返回状态的部分更新。 [12]
- `createReactAgent()` 是 LangGraph 内置的一行式创建工具调用 Agent 的函数；当需要自定义状态、复杂分支、并行或人机交互时，应降级使用 `StateGraph`。 [1]

## 状态管理机制

- 状态（State）由 `TypedDict` 或 Pydantic 模型定义，节点共享该数据结构，每个节点接收当前状态并返回一个字典作为更新。 [4]
- 状态是一个具名通道（named channels）的集合，每个通道有自己的合并规则，由 reducer 函数 `(current, update) => merged` 定义。 [1]
- 默认 reducer 是替换操作，例如返回 `{"answer": "42"}` 会直接覆盖字段。 [4]
- 对于需要累加的字段（如消息列表），使用 `Annotated[list, operator.add]` 实现拼接。 [4]
- 在 Python API 中，使用 `add_messages` 作为 reducer，而在 JavaScript 中需通过 `MessagesAnnotation` 或自定义 reducer 实现。 [1]
- 每个状态键可通过 `Annotated[type, reducer]` 注解添加 reducer 函数，其签名为 `(Value, Value) -> Value`。 [12]
- `context_schema` 用于定义运行作用域上下文的结构，在节点函数中通过 `Runtime[Context]` 参数获取。 [12]
- `config_schema` 参数在 v0.6.0 已废弃，v2.0.0 将移除，应改用 `context_schema`。 [12]

## 图拓扑与节点编排

- **直接边（Direct Edge）**：用 `graph.add_edge("node_a", "node_b")` 表示确定性顺序流程。 [4]
- **条件边（Conditional Edge）**：用 `graph.add_conditional_edges(source_node, routing_function, path_map?)` 实现动态路由。 [4] [5] [6]
- 条件边的路由函数接受当前状态并返回一个字符串（节点名或 `END`）。 [5]
- `path_map` 是可选的映射字典，将路由函数的返回值映射到实际的节点名。如果返回值不在 `path_map` 中，LangGraph 会抛出 `ValueError`。 [5]
- 条件边的核心是状态检查模式，通过读取状态字段进行分支路由。 [6]
- 典型 ReAct 模式：LLM 节点 → 路由函数（若 LLM 最后消息包含工具调用则返回 "tools"，否则返回 `END`）→ 工具节点 → 边回 LLM 节点形成循环。 [4] [7]
- 子图（Subgraph）是一个编译后的 `StateGraph` 实例，可以作为另一个图的节点通过 `.addNode()` 添加。 [2]
- 子图与父图共享状态形状时，状态透明传递，更新通过父图的 reducers 合并。 [2]
- 子图拥有封装的状态作用域，不自动与父图共享；需通过重叠的 State 键（共享通道）或状态转换函数进行显式通信。 [11]
- 节点应具有单一职责，例如 LLM 决策节点、工具执行节点或验证节点。 [4]

## 编译与执行

- 编译后的图实现了 `Runnable` 接口，提供 `.invoke()`, `.stream()`, `.batch()`, `.streamEvents()` 等方法。 [2]
- `.stream()` 在每个节点运行后触发，粒度是节点；`.streamEvents()` 触发每个内部事件，粒度是 token。 [2]
- `.stream()` 接受 `streamMode` 配置，可选 `"updates"`、`"values"` 等，可以传入数组同时获取多个模式。 [2]
- `.streamEvents()` 必须传递 `version: "v2"` 参数。 [2]

## 持久化与检查点

- Checkpointers 将线程的图状态作为检查点持久化，用于短期、线程级的内存（对话连续性、人机交互、时间旅行、容错）。 [8]
- Stores 将应用程序定义的数据持久化在图状态之外，用于长期、跨线程的内存（用户偏好、事实、共享知识）。 [8]
- 图编译时需传入 `checkpointer` 以启用持久化。 [8] [10]
- 检查点系统保存每一步的完整图状态，通过 `thread_id` 区分不同线程的对话历史。 [4] [9]
- `MemorySaver` 和 `InMemorySaver`：将检查点存储在内存中，进程重启后丢失，适用于开发环境。 [8] [9] [10]
- `SqliteSaver`：基于本地文件，适用于开发环境，状态跨进程重启持久。 [9]
- `PostgresSaver`：基于 PostgreSQL，支持异步（`AsyncPostgresSaver`），适用于生产环境。 [8] [9] [10]
- 检查点积累会增加延迟和存储成本，需定期清理或设置保留策略。 [8]
- 默认序列化器为 `JsonPlusSerializer`，敏感数据可使用 `EncryptedSerializer` 包装。 [10]
- 自定义检查点存储器需继承 `BaseCheckpointSaver` 并实现 `get_tuple`、`list`、`put`、`put_writes`、`delete_thread` 等方法。 [10]
- 开发环境使用 `MemorySaver`（内存型）；生产环境使用 `PostgresSaver`（来自 `langgraph-checkpoint-postgres`）实现持久化。 [4]

## 常见陷阱与最佳实践

- **中断与副作用**：绝对避免在 `interrupt(value)` 函数调用前放置副作用代码（如外部 API 调用、数据库写入），否则因节点重执行导致意外后果；最佳实践是将副作用代码放在 `interrupt` 之后或单独节点中。 [11]
- **节点重执行**：从 `interrupt` 恢复时会重新执行整个节点函数，而非仅从 `interrupt(value)` 调用后的下一行；节点逻辑需设计为幂等或能优雅处理完全重执行。 [11]
- **无控制循环**：节点间通过状态传递且无显式计数器或终止条件，压力测试显示每5分钟内存增长约200MB直至 Python MemoryError，可通过设置最大迭代次数或目标完成检查来缓解。 [13]
- **人机交互中断粒度**：关注“如何中断”而非“何时中断”，应映射到关键业务步骤如高风险工具调用前或最终决策点。 [13]
- **版本变更**：LangGraph 版本变化导致工具调用异常处理不同；0.x 版本允许直接抛出 `ToolException`，1.x 版本要求将业务异常转换为正常返回值，需查阅版本文档调整代码。 [13]
- **子图状态不匹配**：嵌套子图与父图的状态结构不匹配会导致数据缺失或工作流停滞，需在所有层级定义一致的状态模式并使用状态变换器映射数据。 [13]
- **依赖版本冲突**：langchain-openai 要求 openai>=2，可破坏使用旧版 openai 的项目，建议使用虚拟环境并固定稳定版本如 LangGraph 0.1.24。 [13]

## 来源

- https://matt-harrison.com/posts/9-5-26-langgraph-part-1/
- https://matt-harrison.com/posts/9-5-26-langgraph-part-2/
- https://docs.aws.amazon.com/cdk/api/v2/python/aws_cdk.aws_stepfunctions/StateGraph.html
- https://devops.gheware.com/blog/posts/langgraph-stategraph-beginners-guide-2026.html
- https://machinelearningplus.com/gen-ai/langgraph-conditional-edges-routing-decisions/
- https://langchain-tutorials.github.io/langgraph-conditional-edges-state-based-decisions/
- https://dev.to/jamesli/advanced-langgraph-implementing-conditional-edges-and-tool-calling-agents-3pdn
- https://docs.langchain.com/oss/python/langgraph/persistence
- https://langgraphjs.guide/persistence/
- https://reference.langchain.com/python/langgraph/checkpoints
- https://sumanmichael.github.io/langgraph-cheatsheet/cheatsheet/faqs-gotchas/
- https://reference.langchain.com/python/langgraph/graph/state/StateGraph
- https://www.octolinkzl.com/articles/navigating_langgraph_workflows_5_key_pitfalls_and_mitigation_strategies