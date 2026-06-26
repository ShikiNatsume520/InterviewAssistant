# LangGraph 子图与多智能体

LangGraph 支持将复杂工作流拆分为多个子图（subgraph），每个子图拥有独立的状态 schema，主图通过"包装节点（wrapper node）"调用子图，实现状态隔离。

## 子图调用模式

主图节点内通过 `subgraph.invoke(sub_input, config)` 唤醒子图，子图继承 checkpoint 链，便于状态回溯与故障重放。

## 状态隔离原则

主图与子图之间通过预留的输入/输出槽位（slots）交互，避免状态直接交叉污染。主图只负责路由与槽位填充，子图内部逻辑自包含。
