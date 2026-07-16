from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.main.tools import resume_agent as resume_agent_module
from agents.main.tools import resume_resources
from kernel.resumes import (
    ResumeAccessDenied,
    ResumeRepository,
    ResumeValidationError,
)
from server.identity import IdentityThreadStore


def _stores(db_path: Path) -> tuple[IdentityThreadStore, ResumeRepository, str, str]:
    identities = IdentityThreadStore(db_path)
    alice = identities.create_guest_session().principal.id
    bob = identities.create_guest_session().principal.id
    return identities, ResumeRepository(db_path), alice, bob


def test_resume_crud_is_owner_scoped_and_rename_keeps_id(tmp_path: Path) -> None:
    identities, resumes, alice, bob = _stores(tmp_path / "app.sqlite")
    try:
        created = resumes.upload(alice, "后端简历.md", "# 项目\nLangGraph")
        renamed = resumes.rename(alice, created.id, "Agent 后端岗位简历")

        assert renamed.id == created.id
        assert resumes.require(alice, created.id).display_name == "Agent 后端岗位简历"
        assert resumes.list(bob) == []
        with pytest.raises(ResumeAccessDenied):
            resumes.require(bob, created.id)
        with pytest.raises(ResumeAccessDenied):
            resumes.delete(bob, created.id)
    finally:
        resumes.close()
        identities.close()


def test_resume_upload_validates_markdown_and_size(tmp_path: Path) -> None:
    identities, resumes, alice, _ = _stores(tmp_path / "app.sqlite")
    try:
        with pytest.raises(ResumeValidationError):
            resumes.upload(alice, "resume.txt", "text")
        with pytest.raises(ResumeValidationError):
            resumes.upload(alice, "../resume.md", "text")
        with pytest.raises(ResumeValidationError):
            resumes.upload(alice, "resume.md", "x" * (1024 * 1024 + 1))
    finally:
        resumes.close()
        identities.close()


def test_derived_resume_is_idempotent_and_never_overwrites_source(
    tmp_path: Path,
) -> None:
    identities, resumes, alice, _ = _stores(tmp_path / "app.sqlite")
    try:
        source = resumes.upload(alice, "resume.md", "# 项目\n旧描述")
        first = resumes.create_derived(
            alice,
            source.id,
            "# 项目\n新描述",
            "resume:alice:thread-1:tool-call-1",
        )
        repeated = resumes.create_derived(
            alice,
            source.id,
            "# 项目\n不应覆盖第一次保存",
            "resume:alice:thread-1:tool-call-1",
        )

        assert first.id != source.id
        assert repeated.id == first.id
        assert repeated.content == "# 项目\n新描述"
        assert repeated.source_resume_id == source.id
        assert resumes.require(alice, source.id).content == "# 项目\n旧描述"
    finally:
        resumes.close()
        identities.close()


def test_search_has_three_states_and_read_is_paginated(tmp_path: Path) -> None:
    identities, resumes, alice, _ = _stores(tmp_path / "app.sqlite")
    try:
        assert resumes.search(alice, "LangGraph").status == "no_documents"
        created = resumes.upload(
            alice,
            "resume.md",
            "# 项目\nLangGraph 多智能体助手\nFastAPI + React",
        )
        assert resumes.search(alice, "不存在").status == "no_matches"
        result = resumes.search(alice, "langgraph")
        assert result.status == "matches"
        assert result.items[0].matches[0].start_line == 2

        page = resumes.read(alice, created.id, 1, 2)
        assert page.content == "1: # 项目\n2: LangGraph 多智能体助手"
        assert page.has_more is True
        assert page.next_start_line == 3
    finally:
        resumes.close()
        identities.close()


def test_thread_selected_resume_is_persisted_and_cleared_on_delete(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "app.sqlite"
    identities = IdentityThreadStore(db_path)
    resumes = ResumeRepository(db_path)
    try:
        principal = identities.create_guest_session().principal
        thread = identities.create_thread(principal, "简历讨论")
        resume = resumes.upload(principal.id, "resume.md", "# 简历")

        selected = identities.select_resume(principal, thread.id, resume.id)
        assert selected.selected_resume_id == resume.id
        resumes.delete(principal.id, resume.id)
        assert identities.require_thread(principal, thread.id).selected_resume_id is None
    finally:
        resumes.close()
        identities.close()


def test_main_resume_tools_use_runtime_user_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "app.sqlite"
    identities, resumes, alice, bob = _stores(db_path)
    try:
        created = resumes.upload(alice, "resume.md", "# 项目\nLangGraph")
        monkeypatch.setattr(resume_resources, "ResumeRepository", lambda: ResumeRepository(db_path))
        config = {"configurable": {"user_id": bob}}

        listed = resume_resources.list_resumes.invoke({}, config=config)
        assert '"status": "no_documents"' in listed
        with pytest.raises(ResumeAccessDenied):
            resume_resources.read_resume.invoke(
                {"resume_id": created.id}, config=config
            )
    finally:
        resumes.close()
        identities.close()


def test_resume_wrapper_uses_minimal_handoff_and_returns_no_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeRepository:
        def require(self, principal_id: str, resume_id: str) -> SimpleNamespace:
            assert principal_id == "alice"
            assert resume_id == "resume-1"
            return SimpleNamespace(
                id=resume_id,
                display_name="Agent 简历",
                content="# 项目\n源正文",
            )

        def close(self) -> None:
            return None

    class FakeGraph:
        async def ainvoke(
            self, invoke_input: dict[str, object], config: dict[str, object]
        ) -> dict[str, object]:
            captured.update(invoke_input)
            return {
                **invoke_input,
                "outcome": "saved",
                "output_resume_id": "derived-1",
                "output_display_name": "Agent 简历_优化版",
                "last_summary": "完成一项修改",
                "resume_shot": "# 项目\n修改后的完整草稿",
            }

    monkeypatch.setattr(resume_agent_module, "ResumeRepository", FakeRepository)
    monkeypatch.setattr(resume_agent_module, "resume_graph", FakeGraph())
    result = asyncio.run(
        resume_agent_module.resume_agent_node(
            {
                "tool_call": {
                    "id": "tool-1",
                    "args": {
                        "resume_id": "resume-1",
                        "user_request": "优化项目经历",
                    },
                }
            },
            {
                "configurable": {
                    "user_id": "alice",
                    "thread_id": "thread-1",
                }
            },
        )
    )

    assert captured["resume_shot"] == "# 项目\n源正文"
    assert captured["resume_session_id"] == "resume:alice:thread-1:tool-1"
    assert set(captured) == {
        "messages",
        "resume_id",
        "source_display_name",
        "user_request",
        "resume_session_id",
        "resume_shot",
    }
    payload = json.loads(result["messages"][0].content)
    assert payload["output_resume_id"] == "derived-1"
    assert "修改后的完整草稿" not in result["messages"][0].content
