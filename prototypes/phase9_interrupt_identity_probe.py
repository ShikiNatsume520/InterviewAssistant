"""阶段 9 原型：把后续审批放到独立父图节点，隔离旧 resume 值。"""

from __future__ import annotations

import asyncio
from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class ProbeState(TypedDict, total=False):
    outline_decision: dict[str, Any]
    report: str
    knowledge_decision: str


work_started = asyncio.Event()
allow_work = asyncio.Event()


def outline_confirm(_: ProbeState) -> dict[str, Any]:
    value = interrupt(
        {
            "phase": "outline_confirm",
            "interrupt_id": "research-session:outline_confirm:1",
        }
    )
    return {"outline_decision": value}


async def compose_report(_: ProbeState) -> dict[str, str]:
    work_started.set()
    await allow_work.wait()
    return {"report": "ready"}


child_workflow = StateGraph(ProbeState)
child_workflow.add_node("outline_confirm", outline_confirm)
child_workflow.add_node("compose_report", compose_report)
child_workflow.add_edge(START, "outline_confirm")
child_workflow.add_edge("outline_confirm", "compose_report")
child_workflow.add_edge("compose_report", END)
child_graph = child_workflow.compile()


async def research_wrapper(_: ProbeState, config: RunnableConfig) -> dict[str, str]:
    result = await child_graph.ainvoke({}, config)
    return {"report": str(result["report"])}


def knowledge_confirm(_: ProbeState) -> dict[str, str]:
    expected_id = "research-session:research_knowledge_confirm:1"
    value = interrupt(
        {
            "phase": "research_knowledge_confirm",
            "interrupt_id": expected_id,
        }
    )
    if not isinstance(value, dict) or value.get("interrupt_id") != expected_id:
        raise RuntimeError("mismatched interrupt response")
    return {"knowledge_decision": str(value["action"])}


async def main() -> None:
    parent_workflow = StateGraph(ProbeState)
    parent_workflow.add_node("research_wrapper", research_wrapper)
    parent_workflow.add_node("knowledge_confirm", knowledge_confirm)
    parent_workflow.add_edge(START, "research_wrapper")
    parent_workflow.add_edge("research_wrapper", "knowledge_confirm")
    parent_workflow.add_edge("knowledge_confirm", END)
    graph = parent_workflow.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "interrupt-identity-probe"}}

    await graph.ainvoke({}, config)
    awaitable = asyncio.create_task(
        graph.ainvoke(
            Command(
                resume={
                    "phase": "outline_confirm",
                    "interrupt_id": "research-session:outline_confirm:1",
                    "action": "approve",
                }
            ),
            config,
        )
    )
    await work_started.wait()
    awaitable.cancel()
    try:
        await awaitable
    except asyncio.CancelledError:
        pass

    allow_work.set()
    await graph.ainvoke(None, config)
    waiting = await graph.aget_state(config)
    assert waiting.next == ("knowledge_confirm",)
    assert waiting.values["report"] == "ready"
    assert "knowledge_decision" not in waiting.values

    result = await graph.ainvoke(
        Command(
            resume={
                "phase": "research_knowledge_confirm",
                "interrupt_id": "research-session:research_knowledge_confirm:1",
                "action": "reject",
            }
        ),
        config,
    )
    assert result["knowledge_decision"] == "reject"
    print("phase9 interrupt identity probe passed")


if __name__ == "__main__":
    asyncio.run(main())
