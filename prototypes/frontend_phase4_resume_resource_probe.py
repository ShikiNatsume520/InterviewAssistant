"""阶段 4A 探针：用户级简历资源、关键词检索三态与删除约束。"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    id: str


class ResumeRepository:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE resumes (
                id TEXT PRIMARY KEY,
                principal_id TEXT NOT NULL,
                original_name TEXT NOT NULL,
                display_name TEXT NOT NULL,
                content TEXT NOT NULL,
                created_seq INTEGER NOT NULL,
                updated_seq INTEGER NOT NULL
            );
            CREATE TABLE active_resume_sessions (
                resume_id TEXT PRIMARY KEY REFERENCES resumes(id),
                thread_id TEXT NOT NULL
            );
            """
        )
        self.seq = 0

    def upload(self, principal: Principal, name: str, content: str) -> str:
        self.seq += 1
        resume_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO resumes VALUES (?, ?, ?, ?, ?, ?, ?)",
            (resume_id, principal.id, name, name.removesuffix(".md"), content, self.seq, self.seq),
        )
        return resume_id

    def list(self, principal: Principal) -> list[dict[str, object]]:
        rows = self.conn.execute(
            "SELECT id, display_name, created_seq, updated_seq FROM resumes "
            "WHERE principal_id = ? ORDER BY created_seq DESC",
            (principal.id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def rename(self, principal: Principal, resume_id: str, display_name: str) -> None:
        self.seq += 1
        cursor = self.conn.execute(
            "UPDATE resumes SET display_name = ?, updated_seq = ? "
            "WHERE id = ? AND principal_id = ?",
            (display_name, self.seq, resume_id, principal.id),
        )
        if cursor.rowcount != 1:
            raise LookupError("resume not found")

    def search(self, principal: Principal, query: str) -> dict[str, object]:
        documents = self.list(principal)
        if not documents:
            return {"status": "no_documents", "items": []}
        rows = self.conn.execute(
            "SELECT id, display_name, content FROM resumes "
            "WHERE principal_id = ? AND instr(lower(content), lower(?)) > 0",
            (principal.id, query),
        ).fetchall()
        if not rows:
            return {"status": "no_matches", "items": []}
        return {
            "status": "matches",
            "items": [
                {"resume_id": row["id"], "display_name": row["display_name"]}
                for row in rows
            ],
        }

    def read(
        self,
        principal: Principal,
        resume_id: str,
        *,
        start_line: int = 1,
        max_lines: int = 2,
    ) -> dict[str, object]:
        row = self.conn.execute(
            "SELECT display_name, content FROM resumes "
            "WHERE id = ? AND principal_id = ?",
            (resume_id, principal.id),
        ).fetchone()
        if row is None:
            raise LookupError("resume not found")
        lines = str(row["content"]).splitlines()
        if start_line < 1 or start_line > max(1, len(lines)):
            raise ValueError("invalid start line")
        selected = lines[start_line - 1 : start_line - 1 + max_lines]
        end_line = start_line + len(selected) - 1
        has_more = end_line < len(lines)
        return {
            "resume_id": resume_id,
            "display_name": row["display_name"],
            "start_line": start_line,
            "end_line": end_line,
            "total_lines": len(lines),
            "has_more": has_more,
            "next_start_line": end_line + 1 if has_more else None,
            "content": "\n".join(
                f"{number}: {line}"
                for number, line in enumerate(selected, start=start_line)
            ),
        }

    def activate(self, principal: Principal, resume_id: str, thread_id: str) -> None:
        if not any(item["id"] == resume_id for item in self.list(principal)):
            raise LookupError("resume not found")
        self.conn.execute(
            "INSERT INTO active_resume_sessions VALUES (?, ?)",
            (resume_id, thread_id),
        )

    def delete(self, principal: Principal, resume_id: str) -> None:
        active = self.conn.execute(
            "SELECT 1 FROM active_resume_sessions WHERE resume_id = ?", (resume_id,)
        ).fetchone()
        if active:
            raise RuntimeError("active resume cannot be deleted")
        cursor = self.conn.execute(
            "DELETE FROM resumes WHERE id = ? AND principal_id = ?",
            (resume_id, principal.id),
        )
        if cursor.rowcount != 1:
            raise LookupError("resume not found")


def main() -> None:
    repo = ResumeRepository()
    alice = Principal("alice")
    bob = Principal("bob")

    assert repo.search(alice, "LangGraph")["status"] == "no_documents"
    resume_id = repo.upload(
        alice,
        "后端简历.md",
        "# 项目\nLangGraph 多智能体助手\nFastAPI + React",
    )
    assert repo.list(bob) == []
    assert repo.search(alice, "不存在")["status"] == "no_matches"
    assert repo.search(alice, "langgraph")["status"] == "matches"
    first_page = repo.read(alice, resume_id)
    assert first_page["content"] == "1: # 项目\n2: LangGraph 多智能体助手"
    assert first_page["has_more"] is True
    assert first_page["next_start_line"] == 3

    try:
        repo.read(bob, resume_id)
    except LookupError:
        pass
    else:
        raise AssertionError("cross-principal read must fail")

    repo.rename(alice, resume_id, "Agent 后端岗位简历")
    assert repo.list(alice)[0]["id"] == resume_id
    assert repo.list(alice)[0]["display_name"] == "Agent 后端岗位简历"

    try:
        repo.rename(bob, resume_id, "越权重命名")
    except LookupError:
        pass
    else:
        raise AssertionError("cross-principal rename must fail")

    repo.activate(alice, resume_id, "thread-1")
    try:
        repo.delete(alice, resume_id)
    except RuntimeError:
        pass
    else:
        raise AssertionError("active resume delete must fail")

    print("phase 4A resume resource probe: PASS")


if __name__ == "__main__":
    main()
