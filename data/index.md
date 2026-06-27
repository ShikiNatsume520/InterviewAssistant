# 知识库索引

> 由 Index Agent 维护，请勿手动编辑。每行一条索引：关键词 | LLM 摘要 | 文件引用。

| 关键词 | 摘要 | 引用 |
|--------|------|------|
| LangGraph, StateGraph, 状态管理, TypedDict, Pydantic, reducer, Annotated, add_messages, 运行时上下文 | LangGraph 状态管理：TypedDict/Pydantic 定义状态作为节点间数据载体，Annotated+reducer 实现字段累加，Runtime 提供运行时上下文 | [langgraph_state.md](data/markdown/langgraph_state.md) |
| LangGraph, subgraph, 子图, 多智能体, 状态隔离, wrapper node, checkpoint, invoke | LangGraph 子图与多智能体：子图独立状态 schema，通过 invoke 调用，继承 checkpoint 链便于回溯与故障重放，主图与子图通过槽位隔离状态 | [langgraph_subgraph.md](data/markdown/langgraph_subgraph.md) |
