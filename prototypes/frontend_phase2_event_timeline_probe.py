"""阶段 2 产品事件：稳定顺序、游标恢复与前端去重探针。"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

EventType = Literal[
    "message.user",
    "message.agent",
    "agent.transition",
    "tool.status",
    "interrupt.requested",
    "interrupt.resolved",
    "task.completed",
    "task.failed",
    "task.cancelled",
]
AgentSource = Literal["user", "main", "resume", "research", "system"]


@dataclass(frozen=True)
class ProductEvent:
    event_id: str
    thread_id: str
    sequence: int
    task_id: str | None
    event_type: EventType
    source: AgentSource
    occurred_at: str
    payload: dict[str, Any]


class EventStore:
    """原型存储：每个 Thread 独立递增 sequence，事件本身只追加。"""

    def __init__(self, path: Path) -> None:
        self._connection = sqlite3.connect(
            path, check_same_thread=False, isolation_level=None
        )
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._connection.executescript(
            """
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS product_events (
                event_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                task_id TEXT,
                event_type TEXT NOT NULL,
                source TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                UNIQUE(thread_id, sequence)
            );
            CREATE INDEX IF NOT EXISTS idx_product_events_thread_sequence
            ON product_events(thread_id, sequence);
            """
        )

    def close(self) -> None:
        """在 Windows 清理临时目录前释放 SQLite 文件句柄。"""
        self._connection.close()

    def append(
        self,
        thread_id: str,
        event_type: EventType,
        source: AgentSource,
        payload: dict[str, Any],
        *,
        task_id: str | None = None,
    ) -> ProductEvent:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                sequence = int(
                    self._connection.execute(
                        """
                        SELECT COALESCE(MAX(sequence), 0) + 1
                        FROM product_events WHERE thread_id = ?
                        """,
                        (thread_id,),
                    ).fetchone()[0]
                )
                event = ProductEvent(
                    event_id=str(uuid.uuid4()),
                    thread_id=thread_id,
                    sequence=sequence,
                    task_id=task_id,
                    event_type=event_type,
                    source=source,
                    occurred_at=datetime.now(UTC).isoformat(),
                    payload=payload,
                )
                self._connection.execute(
                    """
                    INSERT INTO product_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.thread_id,
                        event.sequence,
                        event.task_id,
                        event.event_type,
                        event.source,
                        event.occurred_at,
                        json.dumps(event.payload, ensure_ascii=False),
                    ),
                )
                self._connection.execute("COMMIT")
                return event
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def page(
        self,
        thread_id: str,
        *,
        after_event_id: str | None = None,
        limit: int = 50,
    ) -> list[ProductEvent]:
        after_sequence = 0
        if after_event_id is not None:
            cursor = self._connection.execute(
                """
                SELECT sequence FROM product_events
                WHERE event_id = ? AND thread_id = ?
                """,
                (after_event_id, thread_id),
            ).fetchone()
            if cursor is None:
                raise ValueError("cursor does not belong to this thread")
            after_sequence = int(cursor["sequence"])
        rows = self._connection.execute(
            """
            SELECT * FROM product_events
            WHERE thread_id = ? AND sequence > ?
            ORDER BY sequence ASC LIMIT ?
            """,
            (thread_id, after_sequence, limit),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ProductEvent:
        return ProductEvent(
            event_id=str(row["event_id"]),
            thread_id=str(row["thread_id"]),
            sequence=int(row["sequence"]),
            task_id=str(row["task_id"]) if row["task_id"] is not None else None,
            event_type=row["event_type"],
            source=row["source"],
            occurred_at=str(row["occurred_at"]),
            payload=json.loads(str(row["payload_json"])),
        )


def reduce_events(
    current: list[ProductEvent], incoming: list[ProductEvent]
) -> list[ProductEvent]:
    """模拟 React 数据层：ID 去重，sequence 排序。"""
    merged = {event.event_id: event for event in current}
    merged.update({event.event_id: event for event in incoming})
    return sorted(merged.values(), key=lambda event: event.sequence)


def run_probe() -> None:
    with tempfile.TemporaryDirectory(prefix="phase2-events-") as directory:
        store = EventStore(Path(directory) / "events.sqlite")
        thread_a = "thread-a"
        thread_b = "thread-b"
        task_id = "task-a1"

        user_event = store.append(
            thread_a,
            "message.user",
            "user",
            {"text": "请帮我优化简历"},
            task_id=task_id,
        )
        store.append(
            thread_a,
            "agent.transition",
            "system",
            {"from": "main", "to": "resume"},
            task_id=task_id,
        )

        concurrent_stores = [
            EventStore(Path(directory) / "events.sqlite") for _ in range(12)
        ]
        workers = [
            threading.Thread(
                target=concurrent_stores[index].append,
                args=(
                    thread_a,
                    "tool.status",
                    "resume",
                    {"tool": "grep_replace", "index": index},
                ),
                kwargs={"task_id": task_id},
            )
            for index in range(12)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        for concurrent_store in concurrent_stores:
            concurrent_store.close()

        final = store.append(
            thread_a,
            "message.agent",
            "resume",
            {"text": "已生成第一条修改建议。"},
            task_id=task_id,
        )
        store.append(
            thread_b, "message.user", "user", {"text": "另一个会话"}
        )

        all_events = store.page(thread_a, limit=100)
        assert [event.sequence for event in all_events] == list(
            range(1, len(all_events) + 1)
        )
        assert all(event.thread_id == thread_a for event in all_events)

        first_page = store.page(thread_a, limit=5)
        second_page = store.page(
            thread_a, after_event_id=first_page[-1].event_id, limit=100
        )
        assert first_page + second_page == all_events

        replayed = reduce_events(first_page, first_page + second_page)
        assert replayed == all_events
        assert replayed[0] == user_event
        assert replayed[-1] == final

        foreign_cursor = store.page(thread_b)[0].event_id
        try:
            store.page(thread_a, after_event_id=foreign_cursor)
        except ValueError:
            pass
        else:
            raise AssertionError("foreign cursor must be rejected")

        serialized = json.dumps(
            [event.__dict__ for event in all_events], ensure_ascii=False
        )
        assert "api_key" not in serialized.lower()
        print("PASS: independent SQLite connections produced contiguous sequence")
        print("PASS: afterEventId pagination restored the exact timeline")
        print("PASS: duplicate replay was removed by stable event ID")
        print("PASS: a cursor from another thread was rejected")
        print(f"events={len(all_events)}, last_sequence={all_events[-1].sequence}")
        store.close()


if __name__ == "__main__":
    run_probe()
