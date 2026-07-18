"""阶段 9 原型：验证任务取消释放与操作幂等的最小语义。

本文件只验证并发语义，不作为正式服务实现。
"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import uuid
from pathlib import Path

from server.identity import IdentityThreadStore


class OperationLedger:
    """原型用操作账本：同一 principal/operation 只能首次进入执行。"""

    def __init__(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute(
            """
            CREATE TABLE operations (
                principal_id TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY (principal_id, operation_id)
            )
            """
        )

    def begin(self, principal_id: str, operation_id: str) -> str:
        try:
            with self.connection:
                self.connection.execute(
                    "INSERT INTO operations VALUES (?, ?, 'running')",
                    (principal_id, operation_id),
                )
            return "started"
        except sqlite3.IntegrityError:
            row = self.connection.execute(
                "SELECT status FROM operations WHERE principal_id = ? AND operation_id = ?",
                (principal_id, operation_id),
            ).fetchone()
            return str(row[0])

    def complete(self, principal_id: str, operation_id: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE operations SET status = 'completed' WHERE principal_id = ? AND operation_id = ?",
                (principal_id, operation_id),
            )


async def probe_cancellation_releases_principal_lock(database: Path) -> None:
    store = IdentityThreadStore(database)
    session = store.create_guest_session()
    first = store.create_thread(session.principal, "first")
    second = store.create_thread(session.principal, "second")
    first_task_id = str(uuid.uuid4())
    store.acquire_task(session.principal, first.id, first_task_id)

    started = asyncio.Event()

    async def running_stream() -> None:
        try:
            started.set()
            await asyncio.Future()
        finally:
            store.release_task(
                session.principal,
                first_task_id,
                status="interrupted",
            )

    task = asyncio.create_task(running_stream())
    await started.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert store.require_thread(session.principal, first.id).status == "interrupted"
    second_task_id = str(uuid.uuid4())
    store.acquire_task(session.principal, second.id, second_task_id)
    store.release_task(session.principal, second_task_id, status="idle")
    store.close()


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ia-phase9-") as directory:
        await probe_cancellation_releases_principal_lock(Path(directory) / "app.db")

    ledger = OperationLedger()
    principal_id = "guest-a"
    operation_id = str(uuid.uuid4())
    assert ledger.begin(principal_id, operation_id) == "started"
    assert ledger.begin(principal_id, operation_id) == "running"
    ledger.complete(principal_id, operation_id)
    assert ledger.begin(principal_id, operation_id) == "completed"
    assert ledger.begin("guest-b", operation_id) == "started"
    print("phase9 probe passed: cancellation releases lock; operation IDs deduplicate")


if __name__ == "__main__":
    asyncio.run(main())
