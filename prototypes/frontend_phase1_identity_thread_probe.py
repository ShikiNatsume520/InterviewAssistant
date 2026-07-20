"""阶段 1 快速原型：游客/开发人员身份与 Thread 所有权。

本原型只使用内存 SQLite，不启动 FastAPI、不修改正式后端。验证：

1. 游客和开发人员都使用高熵 bearer session；数据库只保存哈希；
2. 开发人员登录同时受服务端开关和开发凭证保护；
3. Thread owner 只能来自后端已解析 principal；
4. 跨 principal 读取、重命名和删除均被拒绝；
5. 同一 principal 同时只能有一个活动长任务。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

PrincipalKind = Literal["guest", "developer"]


class IdentityError(Exception):
    """身份或凭证无效。"""


class AccessDenied(Exception):
    """资源不属于当前 principal。"""


class ActiveTaskConflict(Exception):
    """当前 principal 已有其他活动任务。"""


def now_iso() -> str:
    """返回 UTC ISO 时间。"""
    return datetime.now(UTC).isoformat()


def token_hash(token: str) -> str:
    """哈希 bearer token；正式实现需要再评估是否加 server-side pepper。"""
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    """生成不可猜测的 bearer token。"""
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Principal:
    """后端解析出的身份上下文。"""

    id: str
    kind: PrincipalKind


@dataclass(frozen=True)
class Thread:
    """Thread 展示元数据。"""

    id: str
    title: str
    status: str


class PrototypeStore:
    """内存 SQLite 身份与 Thread 存储。"""

    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE principals (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK (kind IN ('guest', 'developer')),
                created_at TEXT NOT NULL
            );

            CREATE TABLE sessions (
                token_hash TEXT PRIMARY KEY,
                principal_id TEXT NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL
            );

            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                principal_id TEXT NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX idx_threads_principal_updated
            ON threads(principal_id, updated_at DESC);

            CREATE TABLE active_tasks (
                principal_id TEXT PRIMARY KEY REFERENCES principals(id) ON DELETE CASCADE,
                thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
                task_id TEXT NOT NULL,
                started_at TEXT NOT NULL
            );
            """
        )

    def issue_session(self, kind: PrincipalKind) -> tuple[Principal, str]:
        """创建 principal 并仅返回一次明文 session token。"""
        principal = Principal(id=str(uuid.uuid4()), kind=kind)
        with self.conn:
            self.conn.execute(
                "INSERT INTO principals(id, kind, created_at) VALUES (?, ?, ?)",
                (principal.id, principal.kind, now_iso()),
            )
        return principal, self.issue_session_for(principal)

    def issue_session_for(self, principal: Principal) -> str:
        """为已有 principal 签发新的 bearer session。"""
        token = new_token()
        with self.conn:
            self.conn.execute(
                "INSERT INTO sessions(token_hash, principal_id, created_at) VALUES (?, ?, ?)",
                (token_hash(token), principal.id, now_iso()),
            )
        return token

    def get_or_create_developer(self) -> Principal:
        """返回唯一、稳定的本地开发 principal。"""
        row = self.conn.execute(
            "SELECT id, kind FROM principals WHERE kind = 'developer' LIMIT 1"
        ).fetchone()
        if row is not None:
            return Principal(id=str(row["id"]), kind="developer")
        principal = Principal(id="developer-local", kind="developer")
        with self.conn:
            self.conn.execute(
                "INSERT INTO principals(id, kind, created_at) VALUES (?, ?, ?)",
                (principal.id, principal.kind, now_iso()),
            )
        return principal

    def authenticate(self, token: str) -> Principal:
        """通过 bearer token 哈希解析 principal。"""
        row = self.conn.execute(
            """
            SELECT p.id, p.kind
            FROM sessions s JOIN principals p ON p.id = s.principal_id
            WHERE s.token_hash = ?
            """,
            (token_hash(token),),
        ).fetchone()
        if row is None:
            raise IdentityError("invalid bearer token")
        return Principal(id=str(row["id"]), kind=row["kind"])

    def create_thread(self, principal: Principal, title: str) -> Thread:
        """只使用后端 principal 写 owner，不接受客户端 owner_id。"""
        thread = Thread(id=str(uuid.uuid4()), title=title, status="idle")
        timestamp = now_iso()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO threads(id, principal_id, title, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    thread.id,
                    principal.id,
                    thread.title,
                    thread.status,
                    timestamp,
                    timestamp,
                ),
            )
        return thread

    def list_threads(self, principal: Principal) -> list[Thread]:
        """仅列出当前 principal 的 Thread。"""
        rows = self.conn.execute(
            """
            SELECT id, title, status FROM threads
            WHERE principal_id = ? ORDER BY updated_at DESC
            """,
            (principal.id,),
        ).fetchall()
        return [Thread(id=row["id"], title=row["title"], status=row["status"]) for row in rows]

    def require_thread(self, principal: Principal, thread_id: str) -> Thread:
        """读取 Thread 并强制 owner 校验。"""
        row = self.conn.execute(
            "SELECT id, principal_id, title, status FROM threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
        if row is None or not hmac.compare_digest(row["principal_id"], principal.id):
            raise AccessDenied("thread not found")
        return Thread(id=row["id"], title=row["title"], status=row["status"])

    def rename_thread(
        self, principal: Principal, thread_id: str, title: str
    ) -> Thread:
        """校验所有权后重命名。"""
        self.require_thread(principal, thread_id)
        with self.conn:
            self.conn.execute(
                "UPDATE threads SET title = ?, updated_at = ? WHERE id = ?",
                (title, now_iso(), thread_id),
            )
        return self.require_thread(principal, thread_id)

    def delete_thread(self, principal: Principal, thread_id: str) -> None:
        """校验所有权后删除。"""
        self.require_thread(principal, thread_id)
        with self.conn:
            self.conn.execute("DELETE FROM threads WHERE id = ?", (thread_id,))

    def acquire_task(
        self, principal: Principal, thread_id: str, task_id: str
    ) -> None:
        """获取 principal 级单活动任务锁。"""
        self.require_thread(principal, thread_id)
        try:
            with self.conn:
                self.conn.execute(
                    """
                    INSERT INTO active_tasks(principal_id, thread_id, task_id, started_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (principal.id, thread_id, task_id, now_iso()),
                )
        except sqlite3.IntegrityError as exc:
            raise ActiveTaskConflict("principal already has an active task") from exc

    def release_task(self, principal: Principal, task_id: str) -> None:
        """只释放当前 principal 且 task_id 匹配的锁。"""
        with self.conn:
            self.conn.execute(
                "DELETE FROM active_tasks WHERE principal_id = ? AND task_id = ?",
                (principal.id, task_id),
            )


class DeveloperLogin:
    """服务端控制的开发人员登录入口。"""

    def __init__(self, *, enabled: bool, access_token: str | None) -> None:
        self.enabled = enabled
        self.access_token = access_token

    def login(self, supplied_token: str, store: PrototypeStore) -> tuple[Principal, str]:
        """验证服务端开关与开发凭证后，签发普通 bearer session。"""
        if not self.enabled or not self.access_token:
            raise IdentityError("developer mode disabled")
        if not hmac.compare_digest(supplied_token, self.access_token):
            raise IdentityError("invalid developer credential")
        principal = store.get_or_create_developer()
        return principal, store.issue_session_for(principal)


def expect_error(error_type: type[Exception], action: object) -> None:
    """断言 callable 抛出指定异常。"""
    try:
        action()  # type: ignore[operator]
    except error_type:
        return
    raise AssertionError(f"expected {error_type.__name__}")


def main() -> None:
    """执行身份、所有权和活动任务锁验证。"""
    store = PrototypeStore()
    guest_a, guest_a_token = store.issue_session("guest")
    guest_b, guest_b_token = store.issue_session("guest")

    assert store.authenticate(guest_a_token) == guest_a
    assert store.authenticate(guest_b_token) == guest_b
    expect_error(IdentityError, lambda: store.authenticate("forged-token"))

    disabled_dev = DeveloperLogin(enabled=False, access_token="dev-secret")
    expect_error(
        IdentityError, lambda: disabled_dev.login("dev-secret", store)
    )
    enabled_dev = DeveloperLogin(enabled=True, access_token="dev-secret")
    expect_error(IdentityError, lambda: enabled_dev.login("wrong", store))
    developer, developer_token = enabled_dev.login("dev-secret", store)
    assert store.authenticate(developer_token) == developer
    assert developer.kind == "developer"
    developer_again, developer_token_again = enabled_dev.login("dev-secret", store)
    assert developer_again == developer
    assert developer_token_again != developer_token
    assert store.authenticate(developer_token_again) == developer

    thread_a1 = store.create_thread(guest_a, "A 的会话 1")
    thread_a2 = store.create_thread(guest_a, "A 的会话 2")
    thread_b1 = store.create_thread(guest_b, "B 的会话 1")
    dev_thread = store.create_thread(developer, "开发调试会话")

    assert {thread.id for thread in store.list_threads(guest_a)} == {
        thread_a1.id,
        thread_a2.id,
    }
    assert {thread.id for thread in store.list_threads(guest_b)} == {thread_b1.id}
    assert {thread.id for thread in store.list_threads(developer)} == {dev_thread.id}

    expect_error(AccessDenied, lambda: store.require_thread(guest_b, thread_a1.id))
    expect_error(
        AccessDenied,
        lambda: store.rename_thread(guest_b, thread_a1.id, "越权重命名"),
    )
    expect_error(AccessDenied, lambda: store.delete_thread(guest_b, thread_a1.id))

    renamed = store.rename_thread(guest_a, thread_a1.id, "A 的新标题")
    assert renamed.title == "A 的新标题"

    store.acquire_task(guest_a, thread_a1.id, "task-a1")
    expect_error(
        ActiveTaskConflict,
        lambda: store.acquire_task(guest_a, thread_a2.id, "task-a2"),
    )
    store.acquire_task(guest_b, thread_b1.id, "task-b1")
    store.release_task(guest_a, "task-a1")
    store.acquire_task(guest_a, thread_a2.id, "task-a2")

    database_dump = "\n".join(store.conn.iterdump())
    assert guest_a_token not in database_dump
    assert guest_b_token not in database_dump
    assert developer_token not in database_dump
    assert developer_token_again not in database_dump
    assert "dev-secret" not in database_dump

    print("PASS: 游客 bearer token 可恢复身份，数据库只保存哈希。")
    print("PASS: 开发人员模式关闭/错误凭证均不能登录。")
    print("PASS: 开发登录复用稳定 principal，只签发新 session，不保存开发凭证明文。")
    print("PASS: Thread 列表、读取、重命名和删除均强制 owner。")
    print("PASS: 游客与开发人员 Thread 作用域隔离。")
    print("PASS: 同一 principal 只能持有一个活动任务锁。")


if __name__ == "__main__":
    main()
