"""只用于前端展示的产品事件存储；不参与 LangGraph 执行或恢复。"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

EventType = Literal[
    "message.user",
    "message.agent",
    "citation.list",
    "agent.transition",
    "tool.status",
    "interrupt.requested",
    "interrupt.resolved",
    "resume.result",
    "task.started",
    "task.status",
    "task.completed",
    "task.failed",
    "task.interrupted",
]
EventSource = Literal["user", "main", "resume", "research", "system"]
_FORBIDDEN_PAYLOAD_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "secret",
    "x_api_key",
}


class EventCursorError(ValueError):
    """事件游标不存在，或不属于目标 Thread。"""


@dataclass(frozen=True)
class ProductEvent:
    """一条已持久化、只追加的前端产品事件。"""

    event_id: str
    thread_id: str
    sequence: int
    task_id: str | None
    event_type: EventType
    source: EventSource
    occurred_at: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class EventPage:
    """事件游标分页结果。"""

    events: list[ProductEvent]
    has_more: bool


class ProductEventStore:
    """SQLite 产品事件仓库；事件永远不会被读取为图输入。"""

    def __init__(self, db_path: Path | str) -> None:
        """打开应用数据库并初始化产品事件表。"""
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            str(path), check_same_thread=False, isolation_level=None
        )
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._setup()

    def close(self) -> None:
        """关闭 SQLite 连接。"""
        with self._lock:
            self._connection.close()

    def _setup(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS product_events (
                    event_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL
                        REFERENCES threads(id) ON DELETE CASCADE,
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
            schema_row = self._connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'product_events'"
            ).fetchone()
            if schema_row is not None and "threads_legacy" in str(schema_row["sql"]):
                self._repair_thread_foreign_key()

    def _repair_thread_foreign_key(self) -> None:
        """修复旧版 Thread 表迁移遗留的 product_events 外键目标。"""
        self._connection.executescript(
            """
            PRAGMA foreign_keys = OFF;
            BEGIN IMMEDIATE;
            ALTER TABLE product_events RENAME TO product_events_legacy;
            DROP INDEX IF EXISTS idx_product_events_thread_sequence;
            CREATE TABLE product_events (
                event_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                task_id TEXT,
                event_type TEXT NOT NULL,
                source TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                UNIQUE(thread_id, sequence)
            );
            INSERT INTO product_events(
                event_id, thread_id, sequence, task_id, event_type,
                source, occurred_at, payload_json
            )
            SELECT event_id, thread_id, sequence, task_id, event_type,
                   source, occurred_at, payload_json
            FROM product_events_legacy;
            DROP TABLE product_events_legacy;
            CREATE INDEX idx_product_events_thread_sequence
            ON product_events(thread_id, sequence);
            COMMIT;
            PRAGMA foreign_keys = ON;
            """
        )

    def append(
        self,
        thread_id: str,
        event_type: EventType,
        source: EventSource,
        payload: dict[str, Any],
        *,
        task_id: str | None = None,
    ) -> ProductEvent:
        """原子追加事件并分配 Thread 内严格递增的 sequence。"""
        if _contains_forbidden_key(payload):
            raise ValueError("product event payload contains a credential field")
        payload_json = json.dumps(payload, ensure_ascii=False, default=str)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0) + 1
                    FROM product_events WHERE thread_id = ?
                    """,
                    (thread_id,),
                ).fetchone()
                sequence = int(row[0])
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
                    INSERT INTO product_events(
                        event_id, thread_id, sequence, task_id, event_type,
                        source, occurred_at, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.thread_id,
                        event.sequence,
                        event.task_id,
                        event.event_type,
                        event.source,
                        event.occurred_at,
                        payload_json,
                    ),
                )
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
        return event

    def page(
        self,
        thread_id: str,
        *,
        after_event_id: str | None = None,
        before_event_id: str | None = None,
        limit: int = 50,
    ) -> EventPage:
        """按游标读取事件；向前补齐与向上翻页不能同时使用。"""
        if after_event_id is not None and before_event_id is not None:
            raise EventCursorError("afterEventId and beforeEventId are exclusive")
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")

        if before_event_id is not None:
            sequence = self._cursor_sequence(thread_id, before_event_id)
            rows = self._fetch_before(thread_id, sequence, limit + 1)
            has_more = len(rows) > limit
            selected = list(reversed(rows[:limit]))
        elif after_event_id is not None:
            sequence = self._cursor_sequence(thread_id, after_event_id)
            rows = self._fetch_after(thread_id, sequence, limit + 1)
            has_more = len(rows) > limit
            selected = rows[:limit]
        else:
            rows = self._fetch_latest(thread_id, limit + 1)
            has_more = len(rows) > limit
            selected = list(reversed(rows[:limit]))
        return EventPage(
            events=[self._from_row(row) for row in selected], has_more=has_more
        )

    def _cursor_sequence(self, thread_id: str, event_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT sequence FROM product_events
                WHERE thread_id = ? AND event_id = ?
                """,
                (thread_id, event_id),
            ).fetchone()
        if row is None:
            raise EventCursorError("event cursor not found in thread")
        return int(row["sequence"])

    def _fetch_after(
        self, thread_id: str, sequence: int, limit: int
    ) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self._connection.execute(
                    """
                    SELECT * FROM product_events
                    WHERE thread_id = ? AND sequence > ?
                    ORDER BY sequence ASC LIMIT ?
                    """,
                    (thread_id, sequence, limit),
                ).fetchall()
            )

    def _fetch_before(
        self, thread_id: str, sequence: int, limit: int
    ) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self._connection.execute(
                    """
                    SELECT * FROM product_events
                    WHERE thread_id = ? AND sequence < ?
                    ORDER BY sequence DESC LIMIT ?
                    """,
                    (thread_id, sequence, limit),
                ).fetchall()
            )

    def _fetch_latest(self, thread_id: str, limit: int) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self._connection.execute(
                    """
                    SELECT * FROM product_events
                    WHERE thread_id = ?
                    ORDER BY sequence DESC LIMIT ?
                    """,
                    (thread_id, limit),
                ).fetchall()
            )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ProductEvent:
        return ProductEvent(
            event_id=str(row["event_id"]),
            thread_id=str(row["thread_id"]),
            sequence=int(row["sequence"]),
            task_id=str(row["task_id"]) if row["task_id"] is not None else None,
            event_type=cast(EventType, row["event_type"]),
            source=cast(EventSource, row["source"]),
            occurred_at=str(row["occurred_at"]),
            payload=cast(dict[str, Any], json.loads(str(row["payload_json"]))),
        )


def event_to_dict(event: ProductEvent) -> dict[str, Any]:
    """序列化为供 HTTP/SSE 共用的 camelCase 契约。"""
    return {
        "eventId": event.event_id,
        "threadId": event.thread_id,
        "sequence": event.sequence,
        "taskId": event.task_id,
        "type": event.event_type,
        "source": event.source,
        "occurredAt": event.occurred_at,
        "payload": event.payload,
    }


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).lower().replace("-", "_") in _FORBIDDEN_PAYLOAD_KEYS
            or _contains_forbidden_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_key(item) for item in value)
    return False
