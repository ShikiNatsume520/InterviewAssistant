"""阶段 6 原型：验证嵌套子图可同时输出消息流与节点级进度更新。

目标：
1. 主图以 ``subgraphs=True`` 运行时，能区分 Research 子图 namespace；
2. ``stream_mode=["messages", "updates"]`` 同时保留 token 与完整节点更新；
3. 节点更新足以由服务端单向转换为产品事件，图无需读取产品事件；
4. 验证继承式子图的 checkpoint 粒度是否能细化到单个来源。
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessageChunk
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages


class ResearchProbeState(TypedDict, total=False):
    messages: Annotated[list[Any], add_messages]
    phase: str
    urls: list[str]
    cursor: int
    sources: list[dict[str, str]]


def discover(_: ResearchProbeState) -> dict[str, Any]:
    return {
        "phase": "fetching",
        "urls": ["https://example.test/a", "https://example.test/b"],
        "cursor": 0,
        "sources": [],
    }


def fetch_one(state: ResearchProbeState) -> dict[str, Any]:
    cursor = state.get("cursor", 0)
    url = state.get("urls", [])[cursor]
    sources = [*state.get("sources", [])]
    sources.append({"url": url, "status": "fetched"})
    return {"sources": sources, "cursor": cursor + 1}


def route_after_fetch(state: ResearchProbeState) -> str:
    if state.get("cursor", 0) < len(state.get("urls", [])):
        return "fetch_one"
    return "finish"


def finish(_: ResearchProbeState) -> dict[str, Any]:
    return {
        "phase": "completed",
        "messages": [AIMessageChunk(content="研究完成")],
    }


class ParentState(TypedDict, total=False):
    messages: Annotated[list[Any], add_messages]
    phase: str
    urls: list[str]
    cursor: int
    sources: list[dict[str, str]]


async def main() -> None:
    research_builder = StateGraph(ResearchProbeState)
    research_builder.add_node("discover", discover)
    research_builder.add_node("fetch_one", fetch_one)
    research_builder.add_node("finish", finish)
    research_builder.add_edge(START, "discover")
    research_builder.add_edge("discover", "fetch_one")
    research_builder.add_conditional_edges(
        "fetch_one",
        route_after_fetch,
        {"fetch_one": "fetch_one", "finish": "finish"},
    )
    research_builder.add_edge("finish", END)
    research = research_builder.compile()

    parent_builder = StateGraph(ParentState)
    parent_builder.add_node("research_agent", research)
    parent_builder.add_edge(START, "research_agent")
    parent_builder.add_edge("research_agent", END)
    graph = parent_builder.compile(checkpointer=InMemorySaver())

    config = {"configurable": {"thread_id": "phase6-progress-probe"}}
    research_updates: list[tuple[str, dict[str, Any]]] = []
    message_chunks: list[str] = []

    async for item in graph.astream(
        {},
        config,
        stream_mode=["messages", "updates"],
        subgraphs=True,
    ):
        namespace, mode, payload = item
        namespace_text = ":".join(namespace)
        if mode == "updates" and namespace_text:
            research_updates.append((namespace_text, payload))
        if mode == "messages":
            message, _metadata = payload
            if isinstance(message, AIMessageChunk) and message.content:
                message_chunks.append(str(message.content))

    history = [snapshot async for snapshot in graph.aget_state_history(config)]
    source_counts = [
        len(snapshot.values.get("sources", []))
        for snapshot in history
        if isinstance(snapshot.values, dict)
    ]

    assert any("discover" in update for _, update in research_updates)
    assert sum("fetch_one" in update for _, update in research_updates) == 2
    assert "研究完成" in message_chunks
    # 继承式子图虽然能流出每个内部节点的 update，但父图只在整个子图这个
    # 超级步完成后保存最终 sources；不会出现单来源完成的父级 checkpoint。
    assert 1 not in source_counts
    assert 2 in source_counts

    print("PASS: messages + updates 可同时观察")
    print("PASS: 嵌套 Research namespace 可识别")
    print("FINDING: 继承式子图内部 update 可见，但父图没有单来源 checkpoint")
    print(f"research updates: {len(research_updates)}")
    print(f"parent checkpoints: {len(history)}")


if __name__ == "__main__":
    asyncio.run(main())
