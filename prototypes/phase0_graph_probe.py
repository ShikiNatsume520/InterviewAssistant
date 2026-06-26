"""快速原型：验证 LangGraph 子图编译与 invoke 的最小链路。

本脚本位于 prototypes/，不污染 src/ 正式代码。
仅用于 Phase 0 验证「StateGraph + context_schema + 子图编译 + ainvoke」链路可用。

运行（在项目根）：
    uv run python prototypes/phase0_graph_probe.py
或：
    .venv/Scripts/python.exe prototypes/phase0_graph_probe.py
"""

from __future__ import annotations

import asyncio
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict as TDTypedDict  # noqa: F401  # 仅占位对比


class ProbeState(TypedDict):
    """原型状态：累加消息计数。"""

    query: str
    count: int


async def echo_node(state: ProbeState) -> dict[str, Any]:
    """最简节点：count +1 并回显 query。"""
    return {"count": state.get("count", 0) + 1, "query": state["query"]}


def build_probe_graph() -> Any:
    """构建并编译最简线性图：START -> echo -> END。"""
    g = StateGraph(ProbeState)
    g.add_node("echo", echo_node)
    g.add_edge(START, "echo")
    g.add_edge("echo", END)
    return g.compile(name="Phase0Probe")


async def main() -> None:
    """运行探针，断言结果符合预期。"""
    graph = build_probe_graph()
    result = await graph.ainvoke({"query": "hello", "count": 0})
    assert result["count"] == 1, f"期望 count==1，实际 {result['count']}"
    assert result["query"] == "hello"
    print("[probe] OK:", result)


if __name__ == "__main__":
    asyncio.run(main())
