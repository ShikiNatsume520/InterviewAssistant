# LangGraph 状态管理

LangGraph 的状态（State）是图在节点间传递数据的载体。状态通过 `TypedDict` 或 Pydantic 定义，并在 `StateGraph` 编译时传入。

## 定义状态

使用 `TypedDict` 定义状态结构是 LangGraph 的推荐做法：

```python
class State(TypedDict):
    messages: list
    count: int
```

## 状态累加器

对于需要跨节点累加而非覆盖的字段，使用 `Annotated` 配合 reducer 函数，例如 `add_messages`：

```python
from typing import Annotated
from langgraph.graph.message import add_messages

class State(TypedDict):
    messages: Annotated[list, add_messages]
```

## 运行时上下文

`Runtime` 对象携带运行时配置（`context_schema` 定义其结构），节点可通过 `runtime.context` 读取按 assistant 或按调用覆盖的参数。
