"""阶段 2 产品事件顺序、游标和隔离测试。"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest
from langchain_core.messages import AIMessageChunk, ToolMessage

from server import app as server_app
from server.events import EventCursorError, ProductEventStore, event_to_dict
from server.identity import IdentityThreadStore


def _stores(db_path: Path) -> tuple[IdentityThreadStore, ProductEventStore, str]:
    identities = IdentityThreadStore(db_path)
    principal = identities.create_guest_session().principal
    thread_id = identities.create_thread(principal, "事件测试").id
    return identities, ProductEventStore(db_path), thread_id


def test_event_sequence_and_forward_backward_pagination(tmp_path: Path) -> None:
    identities, events, thread_id = _stores(tmp_path / "app.sqlite")
    try:
        created = [
            events.append(
                thread_id,
                "task.status",
                "main",
                {"status": f"step-{index}"},
            )
            for index in range(6)
        ]

        first = events.page(thread_id, limit=2)
        assert first.events == created[-2:]
        assert first.has_more is True

        after = events.page(thread_id, after_event_id=created[1].event_id, limit=10)
        assert after.events == created[2:]
        assert after.has_more is False

        before = events.page(thread_id, before_event_id=created[5].event_id, limit=2)
        assert before.events == created[3:5]
        assert before.has_more is True
    finally:
        events.close()
        identities.close()


def test_cursor_cannot_cross_thread_and_reexecution_is_not_deduplicated(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "app.sqlite"
    identities, events, first_thread = _stores(db_path)
    principal = identities.create_guest_session().principal
    second_thread = identities.create_thread(principal, "另一个会话").id
    try:
        foreign = events.append(
            second_thread, "task.started", "main", {"status": "running"}
        )
        with pytest.raises(EventCursorError):
            events.page(first_thread, after_event_id=foreign.event_id)

        first_run = events.append(
            first_thread,
            "tool.status",
            "main",
            {"tool": "search", "status": "completed"},
            task_id="attempt-1",
        )
        events.append(
            first_thread,
            "task.interrupted",
            "main",
            {"status": "interrupted"},
            task_id="attempt-1",
        )
        rerun = events.append(
            first_thread,
            "tool.status",
            "main",
            {"tool": "search", "status": "completed"},
            task_id="attempt-2",
        )

        assert first_run.event_id != rerun.event_id
        assert [event.event_type for event in events.page(first_thread).events] == [
            "tool.status",
            "task.interrupted",
            "tool.status",
        ]
    finally:
        events.close()
        identities.close()


def test_independent_connections_serialize_sequence(tmp_path: Path) -> None:
    db_path = tmp_path / "app.sqlite"
    identities, first_store, thread_id = _stores(db_path)
    stores = [first_store, *[ProductEventStore(db_path) for _ in range(7)]]
    try:
        workers = [
            threading.Thread(
                target=store.append,
                args=(
                    thread_id,
                    "task.status",
                    "main",
                    {"status": "running"},
                ),
            )
            for store in stores
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()

        persisted = first_store.page(thread_id, limit=20).events
        assert [event.sequence for event in persisted] == list(range(1, 9))
    finally:
        for store in stores:
            store.close()
        identities.close()


def test_event_contract_rejects_credential_fields(tmp_path: Path) -> None:
    identities, events, thread_id = _stores(tmp_path / "app.sqlite")
    try:
        with pytest.raises(ValueError, match="credential"):
            events.append(
                thread_id,
                "task.started",
                "main",
                {"runtime": {"api_key": "must-not-persist"}},
            )

        event = events.append(thread_id, "message.user", "user", {"text": "正常消息"})
        assert event_to_dict(event)["eventId"] == event.event_id
    finally:
        events.close()
        identities.close()


class _FakeGraph:
    async def astream(
        self, *args: Any, **kwargs: Any
    ) -> AsyncIterator[tuple[tuple[str, ...], tuple[object, dict[str, Any]]]]:
        del args, kwargs
        yield (
            (),
            (
                AIMessageChunk(content="你好", id="message-1"),
                {"langgraph_node": "chat_node"},
            ),
        )
        yield (
            (),
            (
                AIMessageChunk(content="，世界", id="message-1"),
                {"langgraph_node": "chat_node"},
            ),
        )
        yield (
            (),
            (
                ToolMessage(
                    content="完成", tool_call_id="tool-call-1", name="knowledge_search"
                ),
                {},
            ),
        )
        yield (
            (),
            (
                AIMessageChunk(content="internal-memory-output", id="internal-1"),
                {"langgraph_node": "save_memory"},
            ),
        )

    async def aget_state(self, config: dict[str, Any]) -> SimpleNamespace:
        del config
        return SimpleNamespace(tasks=[], values={}, next=[])


class _FakeRagGraph:
    async def astream(
        self, *args: Any, **kwargs: Any
    ) -> AsyncIterator[tuple[tuple[str, ...], tuple[object, dict[str, Any]]]]:
        del args, kwargs
        yield (
            (),
            (
                ToolMessage(content="候选资料", tool_call_id="rag-1", name="rag_agent"),
                {"langgraph_node": "rag_agent"},
            ),
        )
        yield (
            (),
            (
                AIMessageChunk(
                    content=(
                        "只使用第二份资料[1]。\n\n"
                        "## 参考资料\n"
                        "[1] b.md L3-4"
                    ),
                    id="message-rag",
                ),
                {"langgraph_node": "chat_node"},
            ),
        )

    async def aget_state(self, config: dict[str, Any]) -> SimpleNamespace:
        del config
        return SimpleNamespace(
            tasks=[],
            values={
                "citations": [
                    {
                        "file_path": "a.md",
                        "start_line": 1,
                        "end_line": 2,
                        "content": "A",
                        "score": 0.9,
                    },
                    {
                        "file_path": "b.md",
                        "start_line": 3,
                        "end_line": 4,
                        "content": "B",
                        "score": 0.8,
                    },
                ]
            },
            next=[],
        )


def test_stream_deltas_are_transient_but_complete_events_are_persisted(
    tmp_path: Path,
) -> None:
    identities, events, thread_id = _stores(tmp_path / "app.sqlite")
    server_app._state["events"] = events

    async def collect() -> list[dict[str, str]]:
        return [
            frame
            async for frame in server_app._stream_graph(
                _FakeGraph(),
                {},
                {"configurable": {"thread_id": thread_id}},
                thread_id=thread_id,
                task_id="attempt-1",
                initial_source="main",
            )
        ]

    try:
        frames = asyncio.run(collect())
        bodies = [json.loads(frame["data"]) for frame in frames]
        assert [body["kind"] for body in bodies[:2]] == ["delta", "delta"]

        persisted = events.page(thread_id).events
        assert [event.event_type for event in persisted] == [
            "message.agent",
            "tool.status",
            "task.completed",
        ]
        assert persisted[0].payload["text"] == "你好，世界"
        assert "internal-memory-output" not in repr(persisted)
        assert all(event.event_type != "message.delta" for event in persisted)
    finally:
        server_app._state.pop("events", None)
        events.close()
        identities.close()


def test_stream_emits_only_citations_declared_by_rag_answer(tmp_path: Path) -> None:
    identities, events, thread_id = _stores(tmp_path / "app.sqlite")
    server_app._state["events"] = events

    async def collect() -> None:
        async for _ in server_app._stream_graph(
            _FakeRagGraph(),
            {},
            {"configurable": {"thread_id": thread_id}},
            thread_id=thread_id,
            task_id="attempt-rag",
            initial_source="main",
        ):
            pass

    try:
        asyncio.run(collect())
        persisted = events.page(thread_id).events
        message = next(event for event in persisted if event.event_type == "message.agent")
        citation_list = next(
            event for event in persisted if event.event_type == "citation.list"
        )
        assert message.payload["text"] == "只使用第二份资料[1]。"
        assert citation_list.payload["items"] == [
            {
                "file_path": "b.md",
                "start_line": 3,
                "end_line": 4,
                "content": "B",
                "score": 0.8,
            }
        ]
    finally:
        server_app._state.pop("events", None)
        events.close()
        identities.close()
