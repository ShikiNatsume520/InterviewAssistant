"""游客/开发人员身份、Session、Thread 与活动任务持久化。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

PrincipalKind = Literal["guest", "developer"]
ThreadStatus = Literal["idle", "running", "waiting", "interrupted"]
ActiveMode = Literal["chat", "resume"]
ActiveAgent = Literal["main", "resume", "research"]

GUEST_SESSION_TTL = timedelta(days=30)
DEVELOPER_SESSION_TTL = timedelta(hours=12)


class IdentityError(Exception):
    """Session 或开发凭证无效。"""


class AccessDenied(Exception):
    """资源不存在或不属于当前身份。"""


class ActiveTaskConflict(Exception):
    """当前身份已有活动任务，或目标 Thread 正在执行。"""

    def __init__(self, message: str, *, thread_id: str | None = None) -> None:
        """记录占用锁的 Thread，供客户端给出可操作冲突提示。"""
        super().__init__(message)
        self.thread_id = thread_id


@dataclass(frozen=True)
class Principal:
    """服务端解析出的身份上下文。"""

    id: str
    kind: PrincipalKind


@dataclass(frozen=True)
class AuthSession:
    """已认证 Session；token 仅用于刷新同一个 HttpOnly Cookie。"""

    principal: Principal
    token: str
    expires_at: datetime


@dataclass(frozen=True)
class ThreadRecord:
    """会话元数据。"""

    id: str
    title: str
    status: ThreadStatus
    created_at: str
    updated_at: str
    selected_resume_id: str | None
    active_mode: ActiveMode
    active_agent: ActiveAgent


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_token() -> str:
    return secrets.token_urlsafe(32)


class IdentityThreadStore:
    """SQLite 身份与 Thread 存储，所有资源查询强制 principal 边界。"""

    def __init__(self, db_path: Path | str) -> None:
        """打开数据库并按需初始化阶段 1 表结构。"""
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._setup()
        self._recover_interrupted_tasks()

    def close(self) -> None:
        """关闭 SQLite 连接。"""
        with self._lock:
            self._conn.close()

    def _setup(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                PRAGMA foreign_keys = ON;
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS principals (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL CHECK (kind IN ('guest', 'developer')),
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL
                        REFERENCES principals(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL
                        REFERENCES principals(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL
                        CHECK (status IN ('idle', 'running', 'waiting', 'interrupted')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_threads_principal_updated
                ON threads(principal_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS active_tasks (
                    principal_id TEXT PRIMARY KEY
                        REFERENCES principals(id) ON DELETE CASCADE,
                    thread_id TEXT NOT NULL
                        REFERENCES threads(id) ON DELETE CASCADE,
                    task_id TEXT NOT NULL,
                    started_at TEXT NOT NULL
                );

                """
            )
            columns = {
                str(row["name"])
                for row in self._conn.execute("PRAGMA table_info(threads)").fetchall()
            }
            if "selected_resume_id" not in columns:
                self._conn.execute(
                    "ALTER TABLE threads ADD COLUMN selected_resume_id TEXT"
                )
            if "active_mode" not in columns:
                self._conn.execute(
                    "ALTER TABLE threads ADD COLUMN active_mode TEXT NOT NULL "
                    "DEFAULT 'chat'"
                )
            if "active_agent" not in columns:
                self._conn.execute(
                    "ALTER TABLE threads ADD COLUMN active_agent TEXT NOT NULL "
                    "DEFAULT 'main'"
                )
            schema_row = self._conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'threads'"
            ).fetchone()
            if schema_row is not None and "'waiting'" not in str(schema_row["sql"]):
                self._migrate_threads_for_waiting_status()
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS operations (
                    principal_id TEXT NOT NULL
                        REFERENCES principals(id) ON DELETE CASCADE,
                    operation_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL
                        REFERENCES threads(id) ON DELETE CASCADE,
                    status TEXT NOT NULL
                        CHECK (status IN ('running', 'completed')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (principal_id, operation_id)
                )
                """
            )

    def _migrate_threads_for_waiting_status(self) -> None:
        """扩展旧数据库的 Thread 状态约束，同时保留活动任务。"""
        has_product_events = (
            self._conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'product_events'"
            ).fetchone()
            is not None
        )
        event_setup = """
        event_copy = """
        if has_product_events:
            event_setup = """
            ALTER TABLE product_events RENAME TO product_events_legacy;
            DROP INDEX IF EXISTS idx_product_events_thread_sequence;
            """
            event_copy = """
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
            """
        self._conn.executescript(
            f"""
            PRAGMA foreign_keys = OFF;
            BEGIN IMMEDIATE;
            {event_setup}
            ALTER TABLE active_tasks RENAME TO active_tasks_legacy;
            ALTER TABLE threads RENAME TO threads_legacy;
            DROP INDEX IF EXISTS idx_threads_principal_updated;
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                principal_id TEXT NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK (status IN ('idle', 'running', 'waiting', 'interrupted')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                selected_resume_id TEXT,
                active_mode TEXT NOT NULL DEFAULT 'chat',
                active_agent TEXT NOT NULL DEFAULT 'main'
            );
            INSERT INTO threads(
                id, principal_id, title, status, created_at, updated_at,
                selected_resume_id, active_mode, active_agent
            )
            SELECT id, principal_id, title, status, created_at, updated_at,
                   selected_resume_id, active_mode, active_agent
            FROM threads_legacy;
            CREATE TABLE active_tasks (
                principal_id TEXT PRIMARY KEY REFERENCES principals(id) ON DELETE CASCADE,
                thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
                task_id TEXT NOT NULL,
                started_at TEXT NOT NULL
            );
            INSERT INTO active_tasks SELECT * FROM active_tasks_legacy;
            DROP TABLE active_tasks_legacy;
            DROP TABLE threads_legacy;
            {event_copy}
            CREATE INDEX idx_threads_principal_updated
            ON threads(principal_id, updated_at DESC);
            COMMIT;
            PRAGMA foreign_keys = ON;
            """
        )

    def _recover_interrupted_tasks(self) -> None:
        """启动时释放进程异常退出遗留的锁，并保留可恢复状态。"""
        now = _iso(_now())
        with self._lock:
            self._conn.execute(
                """
                UPDATE threads SET status = 'interrupted', updated_at = ?
                WHERE id IN (SELECT thread_id FROM active_tasks)
                """,
                (now,),
            )
            self._conn.execute("DELETE FROM active_tasks")

    def _create_principal(self, kind: PrincipalKind, principal_id: str) -> Principal:
        principal = Principal(id=principal_id, kind=kind)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO principals(id, kind, created_at) VALUES (?, ?, ?)",
                (principal.id, principal.kind, _iso(_now())),
            )
        return principal

    def _issue_session(self, principal: Principal) -> AuthSession:
        token = _new_token()
        now = _now()
        ttl = (
            DEVELOPER_SESSION_TTL
            if principal.kind == "developer"
            else GUEST_SESSION_TTL
        )
        expires_at = now + ttl
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO sessions(
                    token_hash, principal_id, created_at, expires_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    _token_hash(token),
                    principal.id,
                    _iso(now),
                    _iso(expires_at),
                    _iso(now),
                ),
            )
        return AuthSession(principal=principal, token=token, expires_at=expires_at)

    def create_guest_session(self) -> AuthSession:
        """创建新游客身份并签发 30 天 Session。"""
        principal = self._create_principal("guest", str(uuid.uuid4()))
        return self._issue_session(principal)

    def create_developer_session(
        self,
        supplied_token: str,
        *,
        enabled: bool,
        expected_token: str | None,
    ) -> AuthSession:
        """验证开发模式与访问凭证，签发稳定开发 principal 的 Session。"""
        if not enabled or not expected_token:
            raise IdentityError("developer mode disabled")
        if not hmac.compare_digest(supplied_token, expected_token):
            raise IdentityError("invalid developer credential")
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM principals WHERE kind = 'developer' LIMIT 1"
            ).fetchone()
        principal = (
            Principal(id=str(row["id"]), kind="developer")
            if row is not None
            else self._create_principal("developer", "developer-local")
        )
        return self._issue_session(principal)

    def authenticate(self, token: str) -> AuthSession:
        """认证并滚动续期当前 Session。"""
        digest = _token_hash(token)
        with self._lock:
            row = self._conn.execute(
                """
                SELECT p.id, p.kind, s.expires_at
                FROM sessions s JOIN principals p ON p.id = s.principal_id
                WHERE s.token_hash = ?
                """,
                (digest,),
            ).fetchone()
        if row is None:
            raise IdentityError("invalid session")
        now = _now()
        if _parse_iso(str(row["expires_at"])) <= now:
            with self._lock, self._conn:
                self._conn.execute(
                    "DELETE FROM sessions WHERE token_hash = ?", (digest,)
                )
            raise IdentityError("expired session")
        kind: PrincipalKind = row["kind"]
        ttl = DEVELOPER_SESSION_TTL if kind == "developer" else GUEST_SESSION_TTL
        expires_at = now + ttl
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE sessions SET expires_at = ?, last_seen_at = ?
                WHERE token_hash = ?
                """,
                (_iso(expires_at), _iso(now), digest),
            )
        return AuthSession(
            principal=Principal(id=str(row["id"]), kind=kind),
            token=token,
            expires_at=expires_at,
        )

    def create_thread(self, principal: Principal, title: str) -> ThreadRecord:
        """创建归属当前 principal 的 Thread。"""
        thread_id = str(uuid.uuid4())
        now = _iso(_now())
        clean_title = title.strip() or "新会话"
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO threads(
                    id, principal_id, title, status, created_at, updated_at
                ) VALUES (?, ?, ?, 'idle', ?, ?)
                """,
                (thread_id, principal.id, clean_title, now, now),
            )
        return ThreadRecord(
            thread_id, clean_title, "idle", now, now, None, "chat", "main"
        )

    def list_threads(self, principal: Principal) -> list[ThreadRecord]:
        """列出当前 principal 的全部 Thread。"""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, title, status, created_at, updated_at,
                       selected_resume_id, active_mode, active_agent
                FROM threads WHERE principal_id = ?
                ORDER BY updated_at DESC, rowid DESC
                """,
                (principal.id,),
            ).fetchall()
        return [self._thread_from_row(row) for row in rows]

    def require_thread(self, principal: Principal, thread_id: str) -> ThreadRecord:
        """读取 Thread；不存在或越权均抛同一种异常。"""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, principal_id, title, status, created_at, updated_at,
                       selected_resume_id, active_mode, active_agent
                FROM threads WHERE id = ?
                """,
                (thread_id,),
            ).fetchone()
        if row is None or not hmac.compare_digest(
            str(row["principal_id"]), principal.id
        ):
            raise AccessDenied("thread not found")
        return self._thread_from_row(row)

    def rename_thread(
        self, principal: Principal, thread_id: str, title: str
    ) -> ThreadRecord:
        """重命名当前 principal 的 Thread。"""
        self.require_thread(principal, thread_id)
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("title cannot be empty")
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE threads SET title = ?, updated_at = ? WHERE id = ?",
                (clean_title, _iso(_now()), thread_id),
            )
        return self.require_thread(principal, thread_id)

    def select_resume(
        self, principal: Principal, thread_id: str, resume_id: str | None
    ) -> ThreadRecord:
        """持久化当前 Thread 的前端指定简历。"""
        self.require_thread(principal, thread_id)
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE threads SET selected_resume_id = ?, updated_at = ? WHERE id = ?",
                (resume_id, _iso(_now()), thread_id),
            )
        return self.require_thread(principal, thread_id)

    def set_agent_activity(
        self,
        principal: Principal,
        thread_id: str,
        *,
        active_mode: ActiveMode,
        active_agent: ActiveAgent,
    ) -> ThreadRecord:
        """持久化前端接收者视图；LangGraph checkpoint 仍是执行真相。"""
        self.require_thread(principal, thread_id)
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE threads
                SET active_mode = ?, active_agent = ?, updated_at = ?
                WHERE id = ?
                """,
                (active_mode, active_agent, _iso(_now()), thread_id),
            )
        return self.require_thread(principal, thread_id)

    def delete_thread(self, principal: Principal, thread_id: str) -> None:
        """删除无活动任务的 Thread 元数据。"""
        with self._lock:
            self.ensure_thread_deletable(principal, thread_id)
            self._conn.execute("DELETE FROM threads WHERE id = ?", (thread_id,))

    def ensure_thread_deletable(self, principal: Principal, thread_id: str) -> None:
        """确认 Thread 归属正确且当前没有活动任务。"""
        self.require_thread(principal, thread_id)
        with self._lock:
            active = self._conn.execute(
                "SELECT 1 FROM active_tasks WHERE thread_id = ?", (thread_id,)
            ).fetchone()
        if active is not None:
            raise ActiveTaskConflict("thread has an active task")

    def acquire_task(self, principal: Principal, thread_id: str, task_id: str) -> None:
        """获取 principal 级单活动任务锁，并将 Thread 标为 running。"""
        self.require_thread(principal, thread_id)
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    """
                    INSERT INTO active_tasks(
                        principal_id, thread_id, task_id, started_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (principal.id, thread_id, task_id, _iso(_now())),
                )
                self._conn.execute(
                    """
                    UPDATE threads SET status = 'running', updated_at = ?
                    WHERE id = ?
                    """,
                    (_iso(_now()), thread_id),
                )
        except sqlite3.IntegrityError as exc:
            raise ActiveTaskConflict(
                "principal already has an active task",
                thread_id=self.active_thread_id(principal),
            ) from exc

    def active_thread_id(self, principal: Principal) -> str | None:
        """返回当前身份正在执行的 Thread，供冲突提示和显式取消使用。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT thread_id FROM active_tasks WHERE principal_id = ?",
                (principal.id,),
            ).fetchone()
        return str(row["thread_id"]) if row is not None else None

    def claim_operation(
        self, principal: Principal, thread_id: str, operation_id: str
    ) -> Literal["started", "running", "completed"]:
        """声明一个用户操作；重复 ID 返回既有状态而不再次执行。"""
        self.require_thread(principal, thread_id)
        now = _iso(_now())
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    """
                    INSERT INTO operations(
                        principal_id, operation_id, thread_id, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, 'running', ?, ?)
                    """,
                    (principal.id, operation_id, thread_id, now, now),
                )
            return "started"
        except sqlite3.IntegrityError:
            with self._lock:
                row = self._conn.execute(
                    """
                    SELECT thread_id, status FROM operations
                    WHERE principal_id = ? AND operation_id = ?
                    """,
                    (principal.id, operation_id),
                ).fetchone()
            if row is None or str(row["thread_id"]) != thread_id:
                raise ValueError("operation_id already belongs to another thread")
            status: Literal["running", "completed"] = row["status"]
            return status

    def complete_operation(self, principal: Principal, operation_id: str) -> None:
        """把已被服务端接受的操作标为完成；正文和凭据从不进入账本。"""
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE operations SET status = 'completed', updated_at = ?
                WHERE principal_id = ? AND operation_id = ?
                """,
                (_iso(_now()), principal.id, operation_id),
            )

    def discard_operation(self, principal: Principal, operation_id: str) -> None:
        """请求尚未获得执行锁时撤销声明，使同一操作 ID 可以安全重试。"""
        with self._lock, self._conn:
            self._conn.execute(
                """
                DELETE FROM operations
                WHERE principal_id = ? AND operation_id = ? AND status = 'running'
                """,
                (principal.id, operation_id),
            )

    def release_task(
        self,
        principal: Principal,
        task_id: str,
        *,
        status: Literal["idle", "waiting", "interrupted"],
    ) -> None:
        """释放匹配的活动任务锁，并更新 Thread 状态。"""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT thread_id FROM active_tasks
                WHERE principal_id = ? AND task_id = ?
                """,
                (principal.id, task_id),
            ).fetchone()
        if row is None:
            return
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM active_tasks WHERE principal_id = ? AND task_id = ?",
                (principal.id, task_id),
            )
            self._conn.execute(
                "UPDATE threads SET status = ?, updated_at = ? WHERE id = ?",
                (status, _iso(_now()), str(row["thread_id"])),
            )

    @staticmethod
    def _thread_from_row(row: sqlite3.Row) -> ThreadRecord:
        status: ThreadStatus = row["status"]
        return ThreadRecord(
            id=str(row["id"]),
            title=str(row["title"]),
            status=status,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            selected_resume_id=(
                str(row["selected_resume_id"])
                if row["selected_resume_id"] is not None
                else None
            ),
            active_mode=row["active_mode"],
            active_agent=row["active_agent"],
        )
