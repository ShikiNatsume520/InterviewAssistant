"""阶段 7 个人知识资源、幂等导入和删除一致性测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from agents.index import service
from agents.index.scope import (
    KnowledgeScope,
    personal_collection_name,
    resolve_knowledge_scope,
)
from agents.index.state import IndexRow
from agents.index.tools.index_io import write_index
from kernel.knowledge import KnowledgeAccessDenied, KnowledgeRepository
from server.identity import IdentityThreadStore


class _FakeIndexGraph:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, value: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(value)
        if self.fail:
            raise RuntimeError("probe")
        return value


def _repo(tmp_path: Path) -> tuple[IdentityThreadStore, KnowledgeRepository, str, str]:
    db_path = tmp_path / "app.sqlite"
    identities = IdentityThreadStore(db_path)
    first = identities.create_guest_session().principal.id
    second = identities.create_guest_session().principal.id
    return identities, KnowledgeRepository(db_path), first, second


def test_repository_enforces_principal_and_idempotency(tmp_path: Path) -> None:
    identities, repository, alice, bob = _repo(tmp_path)
    try:
        first = repository.reserve(alice, "研究报告", "research", "stable-key")
        repeated = repository.reserve(alice, "研究报告", "research", "stable-key")
        other = repository.reserve(bob, "研究报告", "research", "stable-key")
        assert repeated.id == first.id
        assert other.id != first.id
        with pytest.raises(KnowledgeAccessDenied):
            repository.require(bob, first.id)
    finally:
        repository.close()
        identities.close()


def test_scope_names_are_stable_and_reject_path_traversal() -> None:
    assert personal_collection_name("guest-a") == personal_collection_name("guest-a")
    assert personal_collection_name("guest-a") != personal_collection_name("guest-b")
    assert "guest-a" not in personal_collection_name("guest-a")
    with pytest.raises(ValueError):
        resolve_knowledge_scope("personal", "../guest-b")


def test_import_reuses_failed_resource_on_checkpoint_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identities, repository, alice, _ = _repo(tmp_path)
    scope = KnowledgeScope(
        "personal",
        alice,
        tmp_path / "documents",
        tmp_path / "index.md",
        tmp_path / "chroma",
        "personal_probe",
    )
    monkeypatch.setattr(service, "resolve_knowledge_scope", lambda *args: scope)
    failed_graph = _FakeIndexGraph(fail=True)
    ready_graph = _FakeIndexGraph()
    try:
        failed = asyncio.run(
            service.import_personal_markdown(
                alice,
                "研究报告",
                "# 内容",
                "research",
                idempotency_key="research:thread:tool",
                repository=repository,
                graph=failed_graph,
            )
        )
        retried = asyncio.run(
            service.import_personal_markdown(
                alice,
                "研究报告",
                "# 内容",
                "research",
                idempotency_key="research:thread:tool",
                repository=repository,
                graph=ready_graph,
            )
        )
        assert failed.status == "failed"
        assert retried.status == "ready"
        assert failed.id == retried.id
        assert len(repository.list(alice)) == 1
        assert (scope.documents_dir / retried.storage_name).exists()
    finally:
        repository.close()
        identities.close()


def test_delete_cleans_index_file_vector_and_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identities, repository, alice, _ = _repo(tmp_path)
    resource = repository.reserve(alice, "个人知识", "upload")
    repository.set_status(alice, resource.id, "ready")
    scope = KnowledgeScope(
        "personal",
        alice,
        tmp_path / "documents",
        tmp_path / "index.md",
        tmp_path / "chroma",
        "personal_probe",
    )
    scope.documents_dir.mkdir()
    (scope.documents_dir / resource.storage_name).write_text("正文", encoding="utf-8")
    write_index(
        scope.index_path,
        [IndexRow(keywords=["知识"], summary="摘要", files=[resource.storage_name])],
    )
    deleted: list[str] = []
    monkeypatch.setattr(service, "resolve_knowledge_scope", lambda *args: scope)
    monkeypatch.setattr(service, "make_persistent_client", lambda path: object())
    monkeypatch.setattr(
        service,
        "delete_resource_chunks",
        lambda client, resource_id, name: deleted.append(resource_id),
    )
    try:
        asyncio.run(
            service.delete_personal_resource(
                alice, resource.id, repository=repository
            )
        )
        assert deleted == [resource.id]
        assert not (scope.documents_dir / resource.storage_name).exists()
        assert resource.storage_name not in scope.index_path.read_text(encoding="utf-8")
        assert repository.list(alice) == []
    finally:
        repository.close()
        identities.close()


def test_delete_failure_keeps_retryable_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identities, repository, alice, _ = _repo(tmp_path)
    resource = repository.reserve(alice, "个人知识", "upload")
    repository.set_status(alice, resource.id, "ready")
    scope = KnowledgeScope(
        "personal",
        alice,
        tmp_path / "documents",
        tmp_path / "index.md",
        tmp_path / "chroma",
        "personal_probe",
    )
    scope.documents_dir.mkdir()
    (scope.documents_dir / resource.storage_name).write_text("正文", encoding="utf-8")
    monkeypatch.setattr(service, "resolve_knowledge_scope", lambda *args: scope)
    monkeypatch.setattr(service, "make_persistent_client", lambda path: object())
    monkeypatch.setattr(
        service,
        "delete_resource_chunks",
        lambda *args: (_ for _ in ()).throw(RuntimeError("vector busy")),
    )
    try:
        with pytest.raises(RuntimeError, match="vector busy"):
            asyncio.run(
                service.delete_personal_resource(
                    alice, resource.id, repository=repository
                )
            )
        retained = repository.require(alice, resource.id)
        assert retained.status == "delete_failed"
        assert retained.failure_reason == "RuntimeError"
        assert (scope.documents_dir / resource.storage_name).exists()
    finally:
        repository.close()
        identities.close()


def test_public_import_and_delete_use_public_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = KnowledgeScope(
        "public",
        None,
        tmp_path / "public-documents",
        tmp_path / "public-index.md",
        tmp_path / "chroma",
        "knowledge_base",
    )
    graph = _FakeIndexGraph()
    deleted_files: list[str] = []
    monkeypatch.setattr(service, "resolve_knowledge_scope", lambda *args: scope)
    monkeypatch.setattr(service, "make_persistent_client", lambda path: object())
    monkeypatch.setattr(
        service,
        "delete_file_chunks",
        lambda client, file_path, name: deleted_files.append(file_path),
    )

    resource_id = asyncio.run(
        service.import_public_markdown(
            "公共测试资料", "# 公共正文", graph=graph
        )
    )
    storage_name = f"{resource_id}.md"
    assert graph.calls == [
        {"scope": "public", "principal_id": "", "target_files": [storage_name]}
    ]
    assert (scope.documents_dir / storage_name).exists()
    assert (
        scope.documents_dir / ".metadata" / f"{resource_id}.name"
    ).read_text(encoding="utf-8") == "公共测试资料"

    write_index(
        scope.index_path,
        [IndexRow(keywords=["公共"], summary="摘要", files=[storage_name])],
    )
    asyncio.run(service.delete_public_resource(storage_name))
    assert deleted_files == [storage_name]
    assert not (scope.documents_dir / storage_name).exists()
    assert storage_name not in scope.index_path.read_text(encoding="utf-8")
