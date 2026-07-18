"""知识导入服务：资源状态、Markdown、Index Agent 与 Chroma 的一致性边界。"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from agents.index.graph import graph as index_graph
from agents.index.scope import resolve_knowledge_scope
from agents.index.state import IndexRow
from agents.index.tools.index_io import load_existing_index, write_index
from agents.index.tools.vectorstore import (
    delete_file_chunks,
    delete_resource_chunks,
    make_persistent_client,
)
from kernel.knowledge import KnowledgeRepository, KnowledgeResource, KnowledgeSourceType
from kernel.persistence import APP_DB_PATH

_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _lock_key(principal_id: str) -> str:
    return f"personal:{principal_id}"


async def import_public_markdown(
    display_name: str,
    content: str,
    *,
    graph: Any = index_graph,
) -> str:
    """导入一份公共 Markdown；仅供 API 完成开发人员鉴权后调用。"""
    if not content.strip():
        raise ValueError("Markdown 内容不能为空")
    if len(content.encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("Markdown 文件不能超过 2 MiB")
    async with _locks["public"]:
        resource_id = str(uuid.uuid4())
        storage_name = f"{resource_id}.md"
        scope = resolve_knowledge_scope("public")
        scope.documents_dir.mkdir(parents=True, exist_ok=True)
        metadata_dir = scope.documents_dir / ".metadata"
        metadata_dir.mkdir(exist_ok=True)
        pending_marker = metadata_dir / f"{resource_id}.pending"
        pending_marker.write_text("indexing", encoding="utf-8")
        (scope.documents_dir / storage_name).write_text(
            content.replace("\r\n", "\n").replace("\r", "\n"), encoding="utf-8"
        )
        try:
            await graph.ainvoke(
                {"scope": "public", "principal_id": "", "target_files": [storage_name]}
            )
        except Exception:
            client = make_persistent_client(scope.chroma_path)
            delete_file_chunks(client, storage_name, scope.collection_name)
            (scope.documents_dir / storage_name).unlink(missing_ok=True)
            pending_marker.unlink(missing_ok=True)
            raise
        (metadata_dir / f"{resource_id}.name").write_text(display_name, encoding="utf-8")
        pending_marker.unlink(missing_ok=True)
        return resource_id


async def reindex_public_resource(
    storage_name: str, *, graph: Any = index_graph
) -> None:
    """串行重新索引一份公共知识。"""
    async with _locks["public"]:
        scope = resolve_knowledge_scope("public")
        if not (scope.documents_dir / storage_name).exists():
            raise FileNotFoundError(storage_name)
        await graph.ainvoke(
            {"scope": "public", "principal_id": "", "target_files": [storage_name]}
        )


async def delete_public_resource(storage_name: str) -> None:
    """串行删除公共文件、索引引用及兼容旧 metadata 的全部向量。"""
    async with _locks["public"]:
        scope = resolve_knowledge_scope("public")
        path = scope.documents_dir / Path(storage_name).name
        if not path.exists():
            raise FileNotFoundError(storage_name)
        client = make_persistent_client(scope.chroma_path)
        delete_file_chunks(client, path.name, scope.collection_name)
        rows = load_existing_index(scope.index_path)
        write_index(scope.index_path, _remove_from_rows(rows, path.name))
        path.unlink()
        metadata = scope.documents_dir / ".metadata" / f"{path.stem}.name"
        metadata.unlink(missing_ok=True)


def _remove_from_rows(rows: list[IndexRow], storage_name: str) -> list[IndexRow]:
    updated: list[IndexRow] = []
    for row in rows:
        files = [name for name in row.files if name != storage_name]
        if files:
            updated.append(row.model_copy(update={"files": files}))
    return updated


async def import_personal_markdown(
    principal_id: str,
    display_name: str,
    content: str,
    source_type: KnowledgeSourceType,
    *,
    idempotency_key: str | None = None,
    repository: KnowledgeRepository | None = None,
    graph: Any = index_graph,
) -> KnowledgeResource:
    """幂等导入个人 Markdown，并等待 Index Agent 完成本次索引。"""
    if not content.strip():
        raise ValueError("Markdown 内容不能为空")
    if len(content.encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("Markdown 文件不能超过 2 MiB")
    own_repository = repository is None
    repo = repository or KnowledgeRepository(APP_DB_PATH)
    try:
        async with _locks[_lock_key(principal_id)]:
            resource = repo.reserve(
                principal_id, display_name, source_type, idempotency_key
            )
            if resource.status == "ready":
                return resource
            scope = resolve_knowledge_scope("personal", principal_id)
            scope.documents_dir.mkdir(parents=True, exist_ok=True)
            path = scope.documents_dir / resource.storage_name
            path.write_text(
                content.replace("\r\n", "\n").replace("\r", "\n"), encoding="utf-8"
            )
            repo.set_status(principal_id, resource.id, "indexing")
            try:
                await graph.ainvoke(
                    {
                        "scope": "personal",
                        "principal_id": principal_id,
                        "target_files": [resource.storage_name],
                    }
                )
            except Exception as exc:
                return repo.set_status(
                    principal_id, resource.id, "failed", type(exc).__name__
                )
            return repo.set_status(principal_id, resource.id, "ready")
    finally:
        if own_repository:
            repo.close()


async def reindex_personal_resource(
    principal_id: str,
    resource_id: str,
    *,
    repository: KnowledgeRepository | None = None,
    graph: Any = index_graph,
) -> KnowledgeResource:
    """重新索引当前 principal 已有的一项个人知识。"""
    own_repository = repository is None
    repo = repository or KnowledgeRepository(APP_DB_PATH)
    try:
        async with _locks[_lock_key(principal_id)]:
            resource = repo.require(principal_id, resource_id)
            scope = resolve_knowledge_scope("personal", principal_id)
            path = scope.documents_dir / resource.storage_name
            if not path.exists():
                return repo.set_status(
                    principal_id, resource_id, "failed", "source file missing"
                )
            repo.set_status(principal_id, resource_id, "indexing")
            try:
                await graph.ainvoke(
                    {
                        "scope": "personal",
                        "principal_id": principal_id,
                        "target_files": [resource.storage_name],
                    }
                )
            except Exception as exc:
                return repo.set_status(
                    principal_id, resource_id, "failed", type(exc).__name__
                )
            return repo.set_status(principal_id, resource_id, "ready")
    finally:
        if own_repository:
            repo.close()


async def delete_personal_resource(
    principal_id: str,
    resource_id: str,
    *,
    repository: KnowledgeRepository | None = None,
) -> None:
    """幂等清理个人知识的向量、关键词索引、原文和元数据。"""
    own_repository = repository is None
    repo = repository or KnowledgeRepository(APP_DB_PATH)
    try:
        async with _locks[_lock_key(principal_id)]:
            resource = repo.set_status(principal_id, resource_id, "deleting")
            scope = resolve_knowledge_scope("personal", principal_id)
            try:
                client = make_persistent_client(scope.chroma_path)
                delete_resource_chunks(client, resource.id, scope.collection_name)
                rows = load_existing_index(scope.index_path)
                write_index(
                    scope.index_path, _remove_from_rows(rows, resource.storage_name)
                )
                path = scope.documents_dir / resource.storage_name
                if path.exists():
                    path.unlink()
                repo.remove_record(principal_id, resource_id)
            except Exception as exc:
                repo.set_status(
                    principal_id, resource_id, "delete_failed", type(exc).__name__
                )
                raise
    finally:
        if own_repository:
            repo.close()
