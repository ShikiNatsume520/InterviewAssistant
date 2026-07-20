"""阶段 7 原型：验证个人知识资源的生命周期和强制隔离边界。

仅使用临时 SQLite/文件目录和内存索引，不导入正式模块、不调用在线 embedding。
"""

from __future__ import annotations

import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Resource:
    id: str
    principal_id: str
    scope: str
    display_name: str
    storage_name: str


class ProbeKnowledgeStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.db = sqlite3.connect(root / "resources.sqlite")
        self.db.execute(
            """
            CREATE TABLE resources (
                id TEXT PRIMARY KEY,
                principal_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                display_name TEXT NOT NULL,
                storage_name TEXT NOT NULL,
                content TEXT NOT NULL
            )
            """
        )

    def import_markdown(
        self, principal_id: str, display_name: str, content: str
    ) -> Resource:
        resource_id = str(uuid.uuid4())
        storage_name = f"{resource_id}.md"
        directory = self.root / "principals" / principal_id / "documents"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / storage_name).write_text(content, encoding="utf-8")
        self.db.execute(
            "INSERT INTO resources VALUES (?, ?, 'personal', ?, ?, ?)",
            (resource_id, principal_id, display_name, storage_name, content),
        )
        self.db.commit()
        return Resource(resource_id, principal_id, "personal", display_name, storage_name)

    def add_public(self, display_name: str, content: str) -> Resource:
        resource_id = str(uuid.uuid4())
        storage_name = f"{resource_id}.md"
        self.db.execute(
            "INSERT INTO resources VALUES (?, 'public', 'public', ?, ?, ?)",
            (resource_id, display_name, storage_name, content),
        )
        self.db.commit()
        return Resource(resource_id, "public", "public", display_name, storage_name)

    def list_visible(self, principal_id: str) -> list[Resource]:
        rows = self.db.execute(
            """
            SELECT id, principal_id, scope, display_name, storage_name
            FROM resources
            WHERE scope = 'public' OR principal_id = ?
            ORDER BY id
            """,
            (principal_id,),
        ).fetchall()
        return [Resource(*row) for row in rows]

    def search(self, principal_id: str, term: str) -> list[Resource]:
        rows = self.db.execute(
            """
            SELECT id, principal_id, scope, display_name, storage_name
            FROM resources
            WHERE (scope = 'public' OR principal_id = ?) AND content LIKE ?
            ORDER BY id
            """,
            (principal_id, f"%{term}%"),
        ).fetchall()
        return [Resource(*row) for row in rows]

    def delete_personal(self, principal_id: str, resource_id: str) -> None:
        row = self.db.execute(
            """
            SELECT storage_name FROM resources
            WHERE id = ? AND principal_id = ? AND scope = 'personal'
            """,
            (resource_id, principal_id),
        ).fetchone()
        if row is None:
            raise PermissionError("resource not owned by principal")
        path = self.root / "principals" / principal_id / "documents" / row[0]
        path.unlink()
        self.db.execute("DELETE FROM resources WHERE id = ?", (resource_id,))
        self.db.commit()


class ProbeSharedState:
    """模拟共享 app/checkpoint/store 文件上的可信 principal 边界。"""

    def __init__(self, root: Path) -> None:
        self.db = sqlite3.connect(root / "shared-state.sqlite")
        self.db.executescript(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                principal_id TEXT NOT NULL
            );
            CREATE TABLE checkpoints (
                thread_id TEXT PRIMARY KEY,
                state TEXT NOT NULL
            );
            CREATE TABLE memories (
                principal_id TEXT NOT NULL,
                fact TEXT NOT NULL
            );
            """
        )

    def seed(self) -> None:
        self.db.executemany(
            "INSERT INTO threads VALUES (?, ?)",
            [("thread-a", "guest-a"), ("thread-b", "guest-b")],
        )
        self.db.executemany(
            "INSERT INTO checkpoints VALUES (?, ?)",
            [("thread-a", "Alice checkpoint"), ("thread-b", "Bob checkpoint")],
        )
        self.db.executemany(
            "INSERT INTO memories VALUES (?, ?)",
            [("guest-a", "Alice fact"), ("guest-b", "Bob fact")],
        )
        self.db.commit()

    def read_checkpoint(self, authenticated_principal: str, thread_id: str) -> str:
        row = self.db.execute(
            """
            SELECT checkpoints.state
            FROM checkpoints
            JOIN threads ON threads.id = checkpoints.thread_id
            WHERE threads.id = ? AND threads.principal_id = ?
            """,
            (thread_id, authenticated_principal),
        ).fetchone()
        if row is None:
            raise PermissionError("thread not owned by authenticated principal")
        return str(row[0])

    def list_memories(self, authenticated_principal: str) -> list[str]:
        rows = self.db.execute(
            "SELECT fact FROM memories WHERE principal_id = ?",
            (authenticated_principal,),
        ).fetchall()
        return [str(row[0]) for row in rows]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ia-phase7-kb-") as tmp:
        root = Path(tmp)
        state = ProbeSharedState(root)
        state.seed()
        assert state.read_checkpoint("guest-a", "thread-a") == "Alice checkpoint"
        try:
            state.read_checkpoint("guest-a", "thread-b")
        except PermissionError:
            pass
        else:
            raise AssertionError("guest-a must not read guest-b checkpoint")
        assert state.list_memories("guest-a") == ["Alice fact"]

        store = ProbeKnowledgeStore(root)
        public = store.add_public("guide.md", "公共 LangGraph 指南")
        alice = store.import_markdown(
            "guest-a", "../../同名知识.md", "Alice 的私有 LangGraph 经验"
        )
        bob = store.import_markdown(
            "guest-b", "../../同名知识.md", "Bob 的私有 LangGraph 经验"
        )

        assert alice.storage_name != bob.storage_name
        assert ".." not in alice.storage_name
        assert {item.id for item in store.list_visible("guest-a")} == {
            public.id,
            alice.id,
        }
        assert {item.id for item in store.search("guest-a", "LangGraph")} == {
            public.id,
            alice.id,
        }
        assert bob.id not in {item.id for item in store.search("guest-a", "LangGraph")}

        try:
            store.delete_personal("guest-a", bob.id)
        except PermissionError:
            pass
        else:
            raise AssertionError("guest-a must not delete guest-b resource")

        store.delete_personal("guest-a", alice.id)
        assert {item.id for item in store.list_visible("guest-a")} == {public.id}
        assert {item.id for item in store.list_visible("guest-b")} == {
            public.id,
            bob.id,
        }
        store.db.close()
        state.db.close()

    print("PASS: 共享 checkpoint 只能在 Thread 通过 principal 归属校验后读取。")
    print("PASS: 共享 Store 的记忆 namespace 不会返回其他 principal 的事实。")
    print("PASS: 同名文件使用 resource_id 落盘，不发生覆盖或路径穿越。")
    print("PASS: 列表、联合检索和删除均强制 public + current principal 边界。")
    print("PASS: 删除一个游客的资源不影响公共库和其他游客。")


if __name__ == "__main__":
    main()
