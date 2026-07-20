"""阶段 7 原型：验证深研入库在超级步重跑和索引失败后的幂等性。"""

from __future__ import annotations

import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ImportResult:
    resource_id: str
    status: str


class ProbeImportService:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.db = sqlite3.connect(root / "app.sqlite")
        self.db.execute(
            """
            CREATE TABLE resources (
                id TEXT PRIMARY KEY,
                principal_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                display_name TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL,
                failure_reason TEXT NOT NULL DEFAULT '',
                UNIQUE(principal_id, idempotency_key)
            )
            """
        )
        self.fail_next_index = False
        self.indexed: set[str] = set()

    def import_research(
        self,
        principal_id: str,
        idempotency_key: str,
        display_name: str,
        content: str,
    ) -> ImportResult:
        """重复调用复用同一资源；failed/pending 资源允许恢复索引。"""
        row = self.db.execute(
            """
            SELECT id, status FROM resources
            WHERE principal_id = ? AND idempotency_key = ?
            """,
            (principal_id, idempotency_key),
        ).fetchone()
        if row is None:
            resource_id = str(uuid.uuid4())
            with self.db:
                self.db.execute(
                    """
                    INSERT INTO resources(
                        id, principal_id, idempotency_key, display_name, content, status
                    ) VALUES (?, ?, ?, ?, ?, 'pending')
                    """,
                    (
                        resource_id,
                        principal_id,
                        idempotency_key,
                        display_name,
                        content,
                    ),
                )
        else:
            resource_id = str(row[0])
            if str(row[1]) == "ready":
                return ImportResult(resource_id, "ready")

        directory = self.root / "knowledge" / "principals" / principal_id / "documents"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{resource_id}.md").write_text(content, encoding="utf-8")
        with self.db:
            self.db.execute(
                "UPDATE resources SET status = 'indexing', failure_reason = '' WHERE id = ?",
                (resource_id,),
            )

        if self.fail_next_index:
            self.fail_next_index = False
            with self.db:
                self.db.execute(
                    """
                    UPDATE resources SET status = 'failed', failure_reason = 'probe failure'
                    WHERE id = ?
                    """,
                    (resource_id,),
                )
            return ImportResult(resource_id, "failed")

        # 模拟按 resource_id upsert；重复执行不会增加第二份向量。
        self.indexed.add(resource_id)
        with self.db:
            self.db.execute(
                "UPDATE resources SET status = 'ready' WHERE id = ?", (resource_id,)
            )
        return ImportResult(resource_id, "ready")

    def resource_count(self, principal_id: str) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) FROM resources WHERE principal_id = ?", (principal_id,)
        ).fetchone()
        return int(row[0])


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ia-phase7-import-") as tmp:
        service = ProbeImportService(Path(tmp))
        stable_key = "research:thread-a:tool-call-42"
        service.fail_next_index = True

        failed = service.import_research(
            "guest-a", stable_key, "Claude Code 权限机制", "# 报告\n正文"
        )
        assert failed.status == "failed"
        assert service.resource_count("guest-a") == 1

        retried = service.import_research(
            "guest-a", stable_key, "Claude Code 权限机制", "# 报告\n正文"
        )
        assert retried.status == "ready"
        assert retried.resource_id == failed.resource_id
        assert service.resource_count("guest-a") == 1
        assert service.indexed == {failed.resource_id}

        repeated_after_success = service.import_research(
            "guest-a", stable_key, "Claude Code 权限机制", "# 报告\n正文"
        )
        assert repeated_after_success == retried
        assert service.resource_count("guest-a") == 1

        other_principal = service.import_research(
            "guest-b", stable_key, "Claude Code 权限机制", "# Bob 报告"
        )
        assert other_principal.resource_id != retried.resource_id
        assert service.resource_count("guest-b") == 1
        service.db.close()

    print("PASS: 索引失败后重试复用同一 resource_id，不产生重复知识。")
    print("PASS: 成功后的超级步重跑直接返回原资源，向量 upsert 保持幂等。")
    print("PASS: 幂等键按 principal 隔离，不会跨用户错误复用资源。")


if __name__ == "__main__":
    main()
