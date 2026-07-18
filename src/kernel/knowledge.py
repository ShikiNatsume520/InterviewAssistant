"""个人知识资源元数据仓库。Markdown 正文保存在用户隔离目录。"""

from __future__ import annotations

import hmac
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from kernel.persistence import APP_DB_PATH

KnowledgeSourceType = Literal["upload", "research"]
KnowledgeStatus = Literal[
    "pending", "indexing", "ready", "failed", "deleting", "delete_failed"
]


class KnowledgeError(RuntimeError):
    """知识资源领域错误基类。"""
    pass


class KnowledgeAccessDenied(KnowledgeError):
    """资源不存在或不属于当前 principal。"""
    pass


class KnowledgeValidationError(KnowledgeError):
    """知识资源输入不合法。"""
    pass


@dataclass(frozen=True)
class KnowledgeResource:
    """个人知识资源的持久化元数据。"""
    id: str
    principal_id: str
    source_type: KnowledgeSourceType
    display_name: str
    storage_name: str
    status: KnowledgeStatus
    failure_reason: str
    created_at: str
    updated_at: str
    idempotency_key: str | None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _display_name(value: str) -> str:
    clean = value.strip()
    if clean.lower().endswith(".md"):
        clean = clean[:-3]
    if not clean or len(clean) > 200 or any(char in clean for char in ("\0", "\r", "\n")):
        raise KnowledgeValidationError("知识名称无效")
    return clean


class KnowledgeRepository:
    """在共享应用数据库中按 principal 管理知识资源元数据。"""

    def __init__(self, db_path: Path | str = APP_DB_PATH) -> None:
        """打开数据库并初始化知识资源表。"""
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS knowledge_resources (
                    id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
                    source_type TEXT NOT NULL CHECK(source_type IN ('upload', 'research')),
                    display_name TEXT NOT NULL,
                    storage_name TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'pending', 'indexing', 'ready', 'failed',
                        'deleting', 'delete_failed'
                    )),
                    failure_reason TEXT NOT NULL DEFAULT '',
                    idempotency_key TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_principal_created
                ON knowledge_resources(principal_id, created_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_idempotency
                ON knowledge_resources(principal_id, idempotency_key)
                WHERE idempotency_key IS NOT NULL;
                """
            )

    def close(self) -> None:
        """关闭数据库连接。"""
        self._conn.close()

    def reserve(
        self,
        principal_id: str,
        display_name: str,
        source_type: KnowledgeSourceType,
        idempotency_key: str | None = None,
    ) -> KnowledgeResource:
        """按幂等键取得既有资源或预留一条 pending 资源。"""
        if idempotency_key:
            row = self._conn.execute(
                "SELECT * FROM knowledge_resources WHERE principal_id = ? AND idempotency_key = ?",
                (principal_id, idempotency_key),
            ).fetchone()
            if row is not None:
                return self._from_row(row)
        resource_id = str(uuid.uuid4())
        now = _now()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO knowledge_resources(
                    id, principal_id, source_type, display_name, storage_name,
                    status, idempotency_key, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    resource_id,
                    principal_id,
                    source_type,
                    _display_name(display_name),
                    f"{resource_id}.md",
                    idempotency_key,
                    now,
                    now,
                ),
            )
        return self.require(principal_id, resource_id)

    def list(self, principal_id: str) -> list[KnowledgeResource]:
        """列出当前 principal 的全部个人知识资源。"""
        rows = self._conn.execute(
            "SELECT * FROM knowledge_resources WHERE principal_id = ? ORDER BY created_at DESC",
            (principal_id,),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def require(self, principal_id: str, resource_id: str) -> KnowledgeResource:
        """读取归属当前 principal 的资源，否则统一拒绝。"""
        row = self._conn.execute(
            "SELECT * FROM knowledge_resources WHERE id = ?", (resource_id,)
        ).fetchone()
        if row is None or not hmac.compare_digest(str(row["principal_id"]), principal_id):
            raise KnowledgeAccessDenied("knowledge resource not found")
        return self._from_row(row)

    def set_status(
        self,
        principal_id: str,
        resource_id: str,
        status: KnowledgeStatus,
        failure_reason: str = "",
    ) -> KnowledgeResource:
        """更新资源状态和精简失败原因。"""
        self.require(principal_id, resource_id)
        with self._conn:
            self._conn.execute(
                "UPDATE knowledge_resources SET status = ?, failure_reason = ?, updated_at = ? WHERE id = ?",
                (status, failure_reason[:500], _now(), resource_id),
            )
        return self.require(principal_id, resource_id)

    def remove_record(self, principal_id: str, resource_id: str) -> None:
        """在外部载荷清理成功后删除最终元数据记录。"""
        self.require(principal_id, resource_id)
        with self._conn:
            self._conn.execute("DELETE FROM knowledge_resources WHERE id = ?", (resource_id,))

    @staticmethod
    def _from_row(row: sqlite3.Row) -> KnowledgeResource:
        return KnowledgeResource(
            id=str(row["id"]),
            principal_id=str(row["principal_id"]),
            source_type=str(row["source_type"]),  # type: ignore[arg-type]
            display_name=str(row["display_name"]),
            storage_name=str(row["storage_name"]),
            status=str(row["status"]),  # type: ignore[arg-type]
            failure_reason=str(row["failure_reason"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            idempotency_key=(
                str(row["idempotency_key"])
                if row["idempotency_key"] is not None
                else None
            ),
        )
