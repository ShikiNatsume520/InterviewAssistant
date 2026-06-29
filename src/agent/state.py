"""主图状态定义（简化版 — 不留子图 I/O 槽）。

与子智能体的通信通过标准 ``ToolMessage`` 完成，不在 ``MainState`` 中
为每个子图预留输入/输出槽位。
"""

from __future__ import annotations

from typing import Annotated

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from rag_agent.state import Citation


class MainState(TypedDict, total=False):
    """主图状态。

    Attributes:
        messages: 对话消息历史（``add_messages`` 累积）。
        citations: 最后检索的结构化引用列表，供后续节点直接读取（如前端渲染）。
    """

    messages: Annotated[list[BaseMessage], add_messages]
    citations: list[Citation]

