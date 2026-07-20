"""阶段 0 探针 2：验证嵌套子图消息事件的来源 namespace。

模拟当前项目的静态 wrapper 模式：父图节点在函数体内直接 ``await`` 模块级
预编译子图。通过 ``astream(..., stream_mode="messages", subgraphs=True)`` 观察
Resume/Research 子图消息能否被稳定区分。
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class MessageState(TypedDict, total=False):
    """父图与探针子图共用的最小消息 state。"""

    messages: Annotated[list[BaseMessage], add_messages]


async def resume_chat(_state: MessageState) -> dict[str, Any]:
    """模拟 Resume Agent 产出消息。"""
    return {"messages": [AIMessage(content="resume-child-token")]}


async def research_chat(_state: MessageState) -> dict[str, Any]:
    """模拟 Research Agent 产出消息。"""
    return {"messages": [AIMessage(content="research-child-token")]}


def build_child(name: str, node: Any) -> Any:
    """构建不带 checkpointer 的模块级子图。"""
    workflow = StateGraph(MessageState)
    workflow.add_node("chat", node)
    workflow.add_edge(START, "chat")
    workflow.add_edge("chat", END)
    return workflow.compile(name=name)


resume_graph = build_child("ResumeProbe", resume_chat)
research_graph = build_child("ResearchProbe", research_chat)


async def resume_agent_node(
    _state: MessageState, config: RunnableConfig
) -> dict[str, Any]:
    """模拟项目中的 Resume wrapper。"""
    await resume_graph.ainvoke(
        {"messages": [HumanMessage(content="resume input")]}, config
    )
    return {"messages": [AIMessage(content="resume-wrapper-done")]}


async def research_agent_node(
    _state: MessageState, config: RunnableConfig
) -> dict[str, Any]:
    """模拟项目中的 Research wrapper。"""
    await research_graph.ainvoke(
        {"messages": [HumanMessage(content="research input")]}, config
    )
    return {"messages": [AIMessage(content="research-wrapper-done")]}


def build_parent() -> Any:
    """构建顺序调用两个 wrapper 的父图。"""
    workflow = StateGraph(MessageState)
    workflow.add_node("resume_agent", resume_agent_node)
    workflow.add_node("research_agent", research_agent_node)
    workflow.add_edge(START, "resume_agent")
    workflow.add_edge("resume_agent", "research_agent")
    workflow.add_edge("research_agent", END)
    return workflow.compile(name="MainProbe", checkpointer=MemorySaver())


async def main() -> None:
    """执行并校验 namespace 来源。"""
    graph = build_parent()
    config: dict[str, Any] = {
        "configurable": {"thread_id": "phase0-subgraph-namespace"}
    }
    seen: list[tuple[tuple[str, ...], str]] = []

    async for namespace, payload in graph.astream(
        {"messages": [HumanMessage(content="start")]},
        config,
        stream_mode="messages",
        subgraphs=True,
    ):
        chunk = payload[0] if isinstance(payload, tuple) else payload
        content = getattr(chunk, "content", "")
        if isinstance(content, str) and content:
            ns = tuple(str(part) for part in namespace)
            seen.append((ns, content))
            print(f"namespace={ns!r} content={content!r}")

    resume_events = [(ns, text) for ns, text in seen if text == "resume-child-token"]
    research_events = [
        (ns, text) for ns, text in seen if text == "research-child-token"
    ]
    wrapper_events = [
        (ns, text) for ns, text in seen if text.endswith("wrapper-done")
    ]

    assert len(resume_events) == 1, resume_events
    assert len(research_events) == 1, research_events
    assert resume_events[0][0], "Resume 子图事件 namespace 不应为空"
    assert research_events[0][0], "Research 子图事件 namespace 不应为空"
    assert resume_events[0][0][0].startswith("resume_agent:"), resume_events[0]
    assert research_events[0][0][0].startswith("research_agent:"), research_events[0]
    assert all(ns == () for ns, _text in wrapper_events), wrapper_events

    print("\n=== 机制结论 ===")
    print("PASS: Resume 子图消息首段 namespace 以 resume_agent: 开头。")
    print("PASS: Research 子图消息首段 namespace 以 research_agent: 开头。")
    print("PASS: 父图 wrapper 消息 namespace 为空，可归类为 main。")


if __name__ == "__main__":
    asyncio.run(main())
