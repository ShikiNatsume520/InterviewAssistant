"""用户级 Markdown 简历资源仓库。"""

from __future__ import annotations

import hmac
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Literal

from kernel.persistence import APP_DB_PATH

MAX_RESUME_BYTES = 1024 * 1024
MAX_READ_LINES = 200
MAX_READ_CHARS = 20_000
MAX_SEARCH_HITS = 10


class ResumeAccessDenied(Exception):
    """简历不存在或不属于当前用户。"""


class ResumeValidationError(ValueError):
    """上传内容或资源操作参数无效。"""


@dataclass(frozen=True)
class ResumeDocument:
    """一份属于指定 principal 的 Markdown 简历。"""

    id: str
    principal_id: str
    original_name: str
    display_name: str
    storage_name: str
    content: str
    created_at: str
    updated_at: str
    source_resume_id: str | None


@dataclass(frozen=True)
class ResumeMetadata:
    """不包含正文的简历列表项。"""

    id: str
    original_name: str
    display_name: str
    created_at: str
    updated_at: str
    source_resume_id: str | None


@dataclass(frozen=True)
class ResumeSearchHit:
    """简历关键词命中的行级片段。"""

    start_line: int
    end_line: int
    content: str


@dataclass(frozen=True)
class ResumeSearchItem:
    """包含一组关键词命中的简历。"""

    resume_id: str
    display_name: str
    matches: list[ResumeSearchHit]


@dataclass(frozen=True)
class ResumeSearchResult:
    """区分无文档、无命中和有命中的检索结果。"""

    status: Literal["no_documents", "no_matches", "matches"]
    items: list[ResumeSearchItem]


@dataclass(frozen=True)
class ResumeReadResult:
    """带行号和分页信息的受控正文读取结果。"""

    resume_id: str
    display_name: str
    start_line: int
    end_line: int
    total_lines: int
    has_more: bool
    next_start_line: int | None
    content: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean_original_name(name: str) -> str:
    cleaned = name.strip()
    if (
        not cleaned
        or PurePath(cleaned).name != cleaned
        or "/" in cleaned
        or "\\" in cleaned
        or not cleaned.lower().endswith(".md")
    ):
        raise ResumeValidationError("只支持安全的 .md 文件名")
    return cleaned


def _clean_display_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or len(cleaned) > 120 or any(char in cleaned for char in "\r\n\0"):
        raise ResumeValidationError("简历显示名称无效")
    return cleaned


class ResumeRepository:
    """在应用 SQLite 中管理用户级简历资源。"""

    def __init__(self, db_path: Path | str = APP_DB_PATH) -> None:
        """打开应用数据库并初始化简历资源表。"""
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._setup()

    def close(self) -> None:
        """关闭数据库连接。"""
        self._conn.close()

    def _setup(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                PRAGMA foreign_keys = ON;
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS resumes (
                    id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL
                        REFERENCES principals(id) ON DELETE CASCADE,
                    original_name TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    storage_name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_resumes_principal_created
                ON resumes(principal_id, created_at DESC);
                """
            )
            columns = {
                str(row["name"])
                for row in self._conn.execute("PRAGMA table_info(resumes)").fetchall()
            }
            if "source_resume_id" not in columns:
                self._conn.execute(
                    "ALTER TABLE resumes ADD COLUMN source_resume_id TEXT"
                )
            if "derivation_key" not in columns:
                self._conn.execute(
                    "ALTER TABLE resumes ADD COLUMN derivation_key TEXT"
                )
            self._conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_resumes_derivation_key
                ON resumes(principal_id, derivation_key)
                WHERE derivation_key IS NOT NULL
                """
            )

    def upload(
        self,
        principal_id: str,
        original_name: str,
        content: str,
        display_name: str | None = None,
    ) -> ResumeDocument:
        """校验并保存当前用户上传的 Markdown。"""
        original = _clean_original_name(original_name)
        if "\0" in content:
            raise ResumeValidationError("Markdown 内容包含非法字符")
        if len(content.encode("utf-8")) > MAX_RESUME_BYTES:
            raise ResumeValidationError("Markdown 文件不能超过 1 MiB")
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        display = _clean_display_name(display_name or original[:-3])
        now = _now()
        resume_id = str(uuid.uuid4())
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        storage_name = f"{stamp}_{original}"
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO resumes(
                    id, principal_id, original_name, display_name, storage_name,
                    content, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resume_id,
                    principal_id,
                    original,
                    display,
                    storage_name,
                    normalized,
                    now,
                    now,
                ),
            )
        return self.require(principal_id, resume_id)

    def create_derived(
        self,
        principal_id: str,
        source_resume_id: str,
        content: str,
        derivation_key: str,
    ) -> ResumeDocument:
        """从只读源简历创建幂等派生版本，重复调用返回同一资源。"""
        source = self.require(principal_id, source_resume_id)
        clean_key = derivation_key.strip()
        if not clean_key:
            raise ResumeValidationError("派生简历幂等键不能为空")
        if "\0" in content:
            raise ResumeValidationError("Markdown 内容包含非法字符")
        if len(content.encode("utf-8")) > MAX_RESUME_BYTES:
            raise ResumeValidationError("Markdown 文件不能超过 1 MiB")
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        existing = self._conn.execute(
            """
            SELECT id FROM resumes
            WHERE principal_id = ? AND derivation_key = ?
            """,
            (principal_id, clean_key),
        ).fetchone()
        if existing is not None:
            return self.require(principal_id, str(existing["id"]))

        now = _now()
        resume_id = str(uuid.uuid4())
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        stem = source.original_name[:-3]
        original_name = _clean_original_name(f"{stem}_优化版.md")
        display_name = _clean_display_name(f"{source.display_name}_优化版")
        storage_name = f"{stamp}_{original_name}"
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO resumes(
                        id, principal_id, original_name, display_name, storage_name,
                        content, created_at, updated_at, source_resume_id,
                        derivation_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        resume_id,
                        principal_id,
                        original_name,
                        display_name,
                        storage_name,
                        normalized,
                        now,
                        now,
                        source_resume_id,
                        clean_key,
                    ),
                )
        except sqlite3.IntegrityError:
            row = self._conn.execute(
                """
                SELECT id FROM resumes
                WHERE principal_id = ? AND derivation_key = ?
                """,
                (principal_id, clean_key),
            ).fetchone()
            if row is None:
                raise
            return self.require(principal_id, str(row["id"]))
        return self.require(principal_id, resume_id)

    def list(self, principal_id: str) -> list[ResumeMetadata]:
        """按上传时间倒序列出当前用户简历。"""
        rows = self._conn.execute(
            """
            SELECT id, original_name, display_name, created_at, updated_at,
                   source_resume_id
            FROM resumes WHERE principal_id = ?
            ORDER BY created_at DESC, rowid DESC
            """,
            (principal_id,),
        ).fetchall()
        return [
            ResumeMetadata(
                id=str(row["id"]),
                original_name=str(row["original_name"]),
                display_name=str(row["display_name"]),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
                source_resume_id=(
                    str(row["source_resume_id"])
                    if row["source_resume_id"] is not None
                    else None
                ),
            )
            for row in rows
        ]

    def require(self, principal_id: str, resume_id: str) -> ResumeDocument:
        """读取当前用户拥有的简历，否则使用统一的不存在错误。"""
        row = self._conn.execute(
            "SELECT * FROM resumes WHERE id = ?", (resume_id,)
        ).fetchone()
        if row is None or not hmac.compare_digest(
            str(row["principal_id"]), principal_id
        ):
            raise ResumeAccessDenied("resume not found")
        return ResumeDocument(
            id=str(row["id"]),
            principal_id=str(row["principal_id"]),
            original_name=str(row["original_name"]),
            display_name=str(row["display_name"]),
            storage_name=str(row["storage_name"]),
            content=str(row["content"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            source_resume_id=(
                str(row["source_resume_id"])
                if row["source_resume_id"] is not None
                else None
            ),
        )

    def rename(
        self, principal_id: str, resume_id: str, display_name: str
    ) -> ResumeMetadata:
        """只修改显示名称，保持内部 ID 和原始名称不变。"""
        self.require(principal_id, resume_id)
        display = _clean_display_name(display_name)
        with self._conn:
            self._conn.execute(
                "UPDATE resumes SET display_name = ?, updated_at = ? WHERE id = ?",
                (display, _now(), resume_id),
            )
        return next(item for item in self.list(principal_id) if item.id == resume_id)

    def delete(self, principal_id: str, resume_id: str) -> None:
        """永久删除当前用户简历，并清除 Thread 的选中引用。"""
        self.require(principal_id, resume_id)
        with self._conn:
            self._conn.execute(
                "UPDATE threads SET selected_resume_id = NULL "
                "WHERE principal_id = ? AND selected_resume_id = ?",
                (principal_id, resume_id),
            )
            self._conn.execute("DELETE FROM resumes WHERE id = ?", (resume_id,))

    def search(
        self, principal_id: str, query: str, resume_id: str | None = None
    ) -> ResumeSearchResult:
        """按关键词检索当前用户的一份或全部简历。"""
        clean_query = query.strip().casefold()
        if not clean_query:
            raise ResumeValidationError("检索关键词不能为空")
        documents = (
            [self.require(principal_id, resume_id)]
            if resume_id is not None
            else [self.require(principal_id, item.id) for item in self.list(principal_id)]
        )
        if not documents:
            return ResumeSearchResult("no_documents", [])
        items: list[ResumeSearchItem] = []
        remaining = MAX_SEARCH_HITS
        for document in documents:
            hits: list[ResumeSearchHit] = []
            for line_number, line in enumerate(document.content.splitlines(), 1):
                if clean_query in line.casefold():
                    hits.append(ResumeSearchHit(line_number, line_number, line))
                    remaining -= 1
                    if remaining == 0:
                        break
            if hits:
                items.append(
                    ResumeSearchItem(document.id, document.display_name, hits)
                )
            if remaining == 0:
                break
        return ResumeSearchResult("matches" if items else "no_matches", items)

    def read(
        self,
        principal_id: str,
        resume_id: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> ResumeReadResult:
        """按行分页读取简历，限制单次进入模型上下文的正文。"""
        document = self.require(principal_id, resume_id)
        lines = document.content.splitlines()
        total = len(lines)
        if start_line < 1 or (total > 0 and start_line > total):
            raise ResumeValidationError("起始行超出简历范围")
        requested_end = end_line if end_line is not None else start_line + MAX_READ_LINES - 1
        if requested_end < start_line:
            raise ResumeValidationError("结束行不能早于起始行")
        capped_end = min(requested_end, start_line + MAX_READ_LINES - 1, total)
        selected: list[str] = []
        char_count = 0
        for number in range(start_line, capped_end + 1):
            rendered = f"{number}: {lines[number - 1]}"
            if selected and char_count + len(rendered) + 1 > MAX_READ_CHARS:
                break
            selected.append(rendered)
            char_count += len(rendered) + 1
        actual_end = start_line + len(selected) - 1 if selected else 0
        has_more = actual_end < total
        return ResumeReadResult(
            resume_id=document.id,
            display_name=document.display_name,
            start_line=start_line,
            end_line=actual_end,
            total_lines=total,
            has_more=has_more,
            next_start_line=actual_end + 1 if has_more else None,
            content="\n".join(selected),
        )
