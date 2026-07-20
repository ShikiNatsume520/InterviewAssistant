"""阶段 9 显式取消与锁释放回归测试。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from server.app import _run_locked_stream, _state, cancel_thread_task
from server.events import ProductEventStore
from server.identity import IdentityThreadStore


@pytest.mark.anyio
async def test_cancel_endpoint_stops_stream_and_releases_lock(tmp_path: Path) -> None:
    identity = IdentityThreadStore(tmp_path / "app.sqlite")
    events = ProductEventStore(tmp_path / "app.sqlite")
    previous_state = dict(_state)
    _state["identity"] = identity
    _state["events"] = events
    try:
        auth = identity.create_guest_session()
        thread = identity.create_thread(auth.principal, "running")
        task_id = "task-phase9"
        operation_id = "operation-phase9"
        assert (
            identity.claim_operation(auth.principal, thread.id, operation_id)
            == "started"
        )
        identity.acquire_task(auth.principal, thread.id, task_id)
        started = asyncio.Event()

        async def source() -> AsyncIterator[dict[str, str]]:
            started.set()
            await asyncio.Future()
            yield {"data": "unreachable"}

        async def consume() -> None:
            async for _ in _run_locked_stream(
                source(),
                auth.principal,
                task_id,
                object(),
                {},
                thread.id,
                "main",
                None,
                operation_id,
            ):
                pass

        stream_task = asyncio.create_task(consume())
        await started.wait()
        response = await cancel_thread_task(thread.id, auth)
        with pytest.raises(asyncio.CancelledError):
            await stream_task

        assert response.status_code == 200
        assert identity.require_thread(auth.principal, thread.id).status == (
            "interrupted"
        )
        assert (
            identity.claim_operation(auth.principal, thread.id, operation_id)
            == "completed"
        )
        identity.acquire_task(auth.principal, thread.id, "next-task")
    finally:
        events.close()
        identity.close()
        _state.clear()
        _state.update(previous_state)
