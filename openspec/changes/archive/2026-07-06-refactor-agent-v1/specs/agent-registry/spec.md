## ADDED Requirements

### Requirement: 显式清单 registry 布线主图
系统 SHALL 通过 `agents/main/registry.py` 中的显式 `REGISTRY` 列表布线主图子智能体。每条 `SubAgentMeta` SHALL 包含 `name`、`tool`、`node`、`route_key`，其中 `node` 是模块级静态 wrapper 函数引用。`build_main_graph` SHALL 遍历 `REGISTRY` 执行 `add_node` / `add_edge` / `path_map` / `ALL_TOOLS`，不得为每个子 agent 硬编码独立的 `add_node` 调用。

#### Scenario: 新增子 agent 只改清单与 wrapper 模块
- **WHEN** 开发者新增一个子 agent
- **THEN** 只需新增一个 wrapper 模块（`@tool` + 静态 wrapper 函数）并在 `REGISTRY` 列表追加一条，SHALL NOT 需要修改 `build_main_graph` 的布线逻辑或 `route_after_chat` 的 if 链

#### Scenario: route_after_chat 查表路由
- **WHEN** `chat_node` 产出含 `tool_calls` 的 AI 消息
- **THEN** `route_after_chat` SHALL 通过查 `ROUTE_TABLE`（由 `REGISTRY` 派生）决定目标节点，SHALL NOT 使用 if-elif 链逐个匹配工具名

### Requirement: wrapper 静态 import 子图以支持 Studio 发现
每个子智能体 wrapper 节点函数 SHALL 在模块顶层 `from <agent>.graph import graph as X` 导入已编译子图实例，并在函数体内以裸名 `X` 引用调用 `ainvoke`。wrapper SHALL NOT 使用惰性 getter、函数体内 import、或函数体内编译子图。

#### Scenario: Studio 展开子图内部节点
- **WHEN** 在 LangGraph Studio 中打开 `main_agent` 图并选中某子 agent 节点
- **THEN** Studio SHALL 能展开该子图的内部节点（`PregelNode.subgraphs` 非空），适用于 `rag_agent`、`resume_agent`、`research_agent` 三个子图

#### Scenario: research_agent 子图可被发现
- **WHEN** 编译主图后检查 `research_agent` 节点的 `PregelNode.subgraphs`
- **THEN** 它 SHALL 非空（含 `research_agent` 子图实例），修复重构前惰性 getter 导致的 Studio 无法展开问题
