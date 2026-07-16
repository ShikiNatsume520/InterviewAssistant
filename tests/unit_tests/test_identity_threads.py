"""阶段 1 身份、Thread 隔离与单任务锁测试。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from server.identity import (
    AccessDenied,
    ActiveTaskConflict,
    IdentityError,
    IdentityThreadStore,
)


def test_guest_session_stores_only_token_hash(tmp_path: Path) -> None:
    db_path = tmp_path / "app.sqlite"
    store = IdentityThreadStore(db_path)
    try:
        auth = store.create_guest_session()
        restored = store.authenticate(auth.token)

        assert restored.principal == auth.principal
        with sqlite3.connect(db_path) as connection:
            stored_hash = connection.execute(
                "SELECT token_hash FROM sessions"
            ).fetchone()[0]
        assert auth.token != stored_hash
        assert auth.token.encode() not in db_path.read_bytes()
    finally:
        store.close()


def test_developer_identity_is_stable_and_credential_is_checked(
    tmp_path: Path,
) -> None:
    store = IdentityThreadStore(tmp_path / "app.sqlite")
    try:
        with pytest.raises(IdentityError):
            store.create_developer_session(
                "wrong", enabled=True, expected_token="correct"
            )

        first = store.create_developer_session(
            "correct", enabled=True, expected_token="correct"
        )
        second = store.create_developer_session(
            "correct", enabled=True, expected_token="correct"
        )

        assert first.principal.id == "developer-local"
        assert second.principal == first.principal
        assert second.token != first.token
    finally:
        store.close()


def test_threads_are_owner_scoped_and_active_task_is_principal_scoped(
    tmp_path: Path,
) -> None:
    store = IdentityThreadStore(tmp_path / "app.sqlite")
    try:
        alice = store.create_guest_session().principal
        bob = store.create_guest_session().principal
        first = store.create_thread(alice, "第一轮")
        second = store.create_thread(alice, "第二轮")

        assert [thread.id for thread in store.list_threads(alice)] == [
            second.id,
            first.id,
        ]
        assert store.list_threads(bob) == []
        with pytest.raises(AccessDenied):
            store.require_thread(bob, first.id)

        store.acquire_task(alice, first.id, "task-1")
        with pytest.raises(ActiveTaskConflict):
            store.acquire_task(alice, second.id, "task-2")
        with pytest.raises(ActiveTaskConflict):
            store.delete_thread(alice, first.id)

        store.release_task(alice, "task-1", status="waiting")
        assert store.require_thread(alice, first.id).status == "waiting"
        store.delete_thread(alice, first.id)
        with pytest.raises(AccessDenied):
            store.require_thread(alice, first.id)
    finally:
        store.close()


def test_restart_releases_stale_task_for_checkpoint_resume(tmp_path: Path) -> None:
    db_path = tmp_path / "app.sqlite"
    first_store = IdentityThreadStore(db_path)
    auth = first_store.create_guest_session()
    thread = first_store.create_thread(auth.principal, "待恢复")
    first_store.acquire_task(auth.principal, thread.id, "abandoned-task")
    first_store.close()

    recovered_store = IdentityThreadStore(db_path)
    try:
        principal = recovered_store.authenticate(auth.token).principal
        assert recovered_store.require_thread(principal, thread.id).status == (
            "interrupted"
        )
        recovered_store.acquire_task(principal, thread.id, "resume-task")
    finally:
        recovered_store.close()


def test_thread_agent_activity_is_persisted(tmp_path: Path) -> None:
    store = IdentityThreadStore(tmp_path / "app.sqlite")
    try:
        principal = store.create_guest_session().principal
        thread = store.create_thread(principal, "简历修改")
        assert thread.active_mode == "chat"
        assert thread.active_agent == "main"

        active = store.set_agent_activity(
            principal,
            thread.id,
            active_mode="resume",
            active_agent="resume",
        )
        assert active.active_mode == "resume"
        assert active.active_agent == "resume"
    finally:
        store.close()
