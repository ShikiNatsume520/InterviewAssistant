"""知识索引作用域解析：公共库共享、个人库按 principal 物理隔离。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from kernel.paths import (
    CHROMA_PATH,
    INDEX_MD_PATH,
    MARKDOWN_DIR,
    PERSONAL_KNOWLEDGE_DIR,
)

KnowledgeScopeKind = Literal["public", "personal"]


@dataclass(frozen=True)
class KnowledgeScope:
    """一个可信知识作用域对应的文件与向量位置。"""
    kind: KnowledgeScopeKind
    principal_id: str | None
    documents_dir: Path
    index_path: Path
    chroma_path: Path
    collection_name: str


def _validate_principal_id(principal_id: str) -> str:
    value = principal_id.strip()
    if not value or any(part in value for part in ("/", "\\", "..", "\0")):
        raise ValueError("invalid principal_id")
    return value


def personal_collection_name(principal_id: str) -> str:
    """生成不暴露 principal 原文的稳定个人 collection 名。"""
    digest = hashlib.sha256(_validate_principal_id(principal_id).encode()).hexdigest()[:24]
    return f"knowledge_personal_{digest}"


def resolve_knowledge_scope(
    kind: KnowledgeScopeKind, principal_id: str | None = None
) -> KnowledgeScope:
    """把可信作用域解析为固定目录和 collection，拒绝任意路径。"""
    if kind == "public":
        return KnowledgeScope(
            kind="public",
            principal_id=None,
            documents_dir=MARKDOWN_DIR,
            index_path=INDEX_MD_PATH,
            chroma_path=CHROMA_PATH,
            collection_name="knowledge_base",
        )
    if principal_id is None:
        raise ValueError("personal scope requires principal_id")
    owner = _validate_principal_id(principal_id)
    base = PERSONAL_KNOWLEDGE_DIR / owner
    return KnowledgeScope(
        kind="personal",
        principal_id=owner,
        documents_dir=base / "documents",
        index_path=base / "index.md",
        chroma_path=CHROMA_PATH,
        collection_name=personal_collection_name(owner),
    )
