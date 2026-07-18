"""阶段 6 Research 状态流水线与产品事件投影测试。"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agents.main.tools import research_agent as research_wrapper
from agents.research.state import ResearchState
from server.app import _research_product_updates

research_graph = importlib.import_module("agents.research.graph")


def _initial_state() -> ResearchState:
    return {
        "gap_topic": "LangGraph 时间旅行",
        "outline": ["LangGraph time travel"],
        "query_cursor": 0,
        "source_cursor": 0,
        "sources": [],
    }


def test_research_processes_each_source_with_stable_statuses(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        research_graph,
        "search",
        lambda query, max_results=3: [
            {"title": "官方文档", "href": "https://example.test/docs", "body": ""}
        ],
    )
    monkeypatch.setattr(
        research_graph,
        "fetch_text",
        lambda url: ("官方文档", "网页正文"),
    )
    monkeypatch.setattr(
        research_graph,
        "get_chat_model",
        lambda model: SimpleNamespace(
            invoke=lambda prompt: SimpleNamespace(content="提炼后的事实")
        ),
    )

    state = _initial_state()
    state.update(research_graph.discover_query_node(state))
    assert state["sources"][0]["status"] == "pending"
    source_id = state["sources"][0]["source_id"]

    state.update(research_graph.prepare_source_node(state))
    assert state["sources"][0]["status"] == "fetching"
    state.update(research_graph.fetch_source_node(state))
    assert state["sources"][0]["status"] == "fetched"
    state.update(research_graph.prepare_distill_node(state))
    assert state["sources"][0]["status"] == "distilling"
    state.update(research_graph.distill_source_node(state))

    assert state["sources"][0]["source_id"] == source_id
    assert state["sources"][0]["status"] == "succeeded"
    assert state["sources"][0]["note"] == "提炼后的事实"
    assert state["current_raw_content"] == ""


def test_compose_report_does_not_write_or_index(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        research_graph,
        "get_chat_model",
        lambda model: SimpleNamespace(
            invoke=lambda prompt: SimpleNamespace(content="# 完整研究报告")
        ),
    )
    state: ResearchState = {
        "gap_topic": "测试主题",
        "sources": [
            {
                "source_id": "source-1",
                "query": "query",
                "url": "https://example.test",
                "title": "来源",
                "status": "succeeded",
                "failure_reason": "",
                "note": "笔记",
            }
        ],
    }

    update = research_graph.compose_report_node(state)

    assert update["report_markdown"] == "# 完整研究报告"
    assert update["knowledge_decision"] == "pending"
    assert update["import_status"] == "not_requested"
    assert "new_file_name" not in update


def test_research_updates_project_report_and_source_without_notes() -> None:
    projected = _research_product_updates(
        {
            "compose_report": {
                "gap_topic": "主题",
                "phase": "waiting_knowledge_decision",
                "report_markdown": "# 报告",
                "report_summary": "摘要",
                "sources": [
                    {
                        "source_id": "source-1",
                        "query": "query",
                        "url": "https://example.test",
                        "title": "来源",
                        "status": "succeeded",
                        "failure_reason": "",
                        "note": "不能进入来源状态事件的内部笔记",
                    }
                ],
            }
        }
    )

    event_types = [event_type for event_type, _ in projected]
    assert "research.source" in event_types
    assert "research.report" in event_types
    source_payload = next(
        payload for event_type, payload in projected if event_type == "research.source"
    )
    assert "note" not in source_payload
    report_payload = next(
        payload for event_type, payload in projected if event_type == "research.report"
    )
    assert report_payload["markdown"] == "# 报告"


@pytest.mark.anyio
async def test_research_graph_waits_for_knowledge_decision(monkeypatch: Any) -> None:
    responses = iter(
        [
            '["LangGraph time travel"]',
            "提炼后的事实",
            "# 完整研究报告",
        ]
    )
    monkeypatch.setattr(research_graph, "connectivity_probe", lambda: True)
    monkeypatch.setattr(
        research_graph,
        "search",
        lambda query, max_results=3: [
            {"title": "官方文档", "href": "https://example.test/docs", "body": ""}
        ],
    )
    monkeypatch.setattr(
        research_graph, "fetch_text", lambda url: ("官方文档", "网页正文")
    )
    monkeypatch.setattr(
        research_graph,
        "get_chat_model",
        lambda model: SimpleNamespace(
            invoke=lambda prompt: SimpleNamespace(content=next(responses))
        ),
    )
    graph = research_graph.build_research_workflow().compile(
        checkpointer=InMemorySaver()
    )
    config = {"configurable": {"thread_id": "research-phase6-flow"}}

    await graph.ainvoke({"gap_topic": "LangGraph 时间旅行"}, config)
    first = await graph.aget_state(config)
    assert first.next == ("outline_confirm",)

    await graph.ainvoke(Command(resume={"action": "approve"}), config)
    waiting = await graph.aget_state(config)
    assert waiting.next == ("knowledge_confirm",)
    assert waiting.values["report_markdown"] == "# 完整研究报告"
    assert waiting.values["knowledge_decision"] == "pending"

    result = await graph.ainvoke(Command(resume={"action": "reject"}), config)
    assert result["knowledge_decision"] == "rejected"
    assert result["import_status"] == "not_requested"
    assert result["approval_status"] == "approved"


@pytest.mark.anyio
async def test_wrapper_keeps_full_report_out_of_main_messages(monkeypatch: Any) -> None:
    async def fake_ainvoke(input_data: Any, config: Any) -> dict[str, Any]:
        del input_data, config
        return {
            "gap_topic": "测试主题",
            "approval_status": "approved",
            "report_summary": "精简摘要",
            "report_markdown": "绝不能进入 MainState.messages 的完整报告",
            "knowledge_decision": "rejected",
            "import_status": "not_requested",
            "sources": [
                {
                    "status": "succeeded",
                }
            ],
        }

    monkeypatch.setattr(research_wrapper.research_graph, "ainvoke", fake_ainvoke)
    result = await research_wrapper.research_agent_node(
        {
            "tool_call": {
                "id": "research-call",
                "args": {"gap_topic": "测试主题"},
            }
        },
        {"configurable": {"thread_id": "thread"}},
    )

    messages = result["messages"]
    assert len(messages) == 1
    assert isinstance(messages[0], ToolMessage)
    assert messages[0].name == "research_agent"
    assert "精简摘要" in messages[0].content
    assert "绝不能进入 MainState.messages 的完整报告" not in messages[0].content


@pytest.mark.anyio
async def test_approved_report_uses_formal_import_service(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def fake_import(*args: Any, **kwargs: Any) -> Any:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(id="knowledge-1", status="ready")

    monkeypatch.setattr(research_graph, "import_personal_markdown", fake_import)
    update = await research_graph.import_knowledge_node(
        {
            "principal_id": "guest-a",
            "thread_id": "thread-a",
            "tool_call_id": "tool-a",
            "proposed_file_name": "研究报告.md",
            "report_markdown": "# 完整报告",
        }
    )

    assert captured["args"][:4] == (
        "guest-a",
        "研究报告.md",
        "# 完整报告",
        "research",
    )
    assert captured["kwargs"]["idempotency_key"] == "research:thread-a:tool-a"
    assert update == {
        "import_status": "completed",
        "knowledge_resource_id": "knowledge-1",
        "phase": "completed",
    }

    projected = _research_product_updates({"import_knowledge": update})
    assert projected[-1][1]["importStatus"] == "completed"
    assert projected[-1][1]["resourceId"] == "knowledge-1"
