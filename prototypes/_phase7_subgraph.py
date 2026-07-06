"""Phase 7 原型: 子图定义模块。

故意放在独立文件, 模拟真实 ``src/rag_agent/graph.py`` 的形态:
模块级编译好子图实例, 供主图 wrapper 模块顶层 import。
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict


class SubState(TypedDict, total=False):
    """子图状态 (最小占位)。"""

    value: str


def _sub_node(state: SubState) -> dict[str, Any]:
    """子图内部节点 (空操作, 原型不实际运行图)。"""
    return {"value": "ok"}


def _build(name: str) -> Any:
    """构建并编译一个两节点子图, 返回 CompiledStateGraph。"""
    g = StateGraph(SubState)
    g.add_node("n", _sub_node)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    return g.compile(name=name)


# 模块级已编译子图实例 (模拟 src/rag_agent/graph.py 末尾的 ``graph = ...compile()``)
graph_a = _build("sub_a")
graph_b = _build("sub_b")
