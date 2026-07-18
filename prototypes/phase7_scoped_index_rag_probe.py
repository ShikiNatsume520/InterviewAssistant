"""阶段 7 原型：验证作用域化 Index/RAG 与资源删除的一致性。

使用临时目录、显式二维向量和 Chroma，不调用在线 embedding 或 LLM。
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import chromadb

Scope = Literal["public", "personal"]


@dataclass(frozen=True)
class KnowledgeScope:
    scope: Scope
    principal_id: str | None
    documents_dir: Path
    index_path: Path
    collection_name: str


def collection_name(scope: Scope, principal_id: str | None) -> str:
    """从可信作用域生成合法稳定的 collection 名，不暴露原 principal。"""
    if scope == "public":
        return "knowledge_public"
    if not principal_id:
        raise ValueError("personal scope requires principal_id")
    digest = hashlib.sha256(principal_id.encode()).hexdigest()[:24]
    return f"knowledge_personal_{digest}"


def resolve_scope(root: Path, scope: Scope, principal_id: str | None) -> KnowledgeScope:
    """只接受作用域和认证 principal，不接受调用方提供任意目录。"""
    if scope == "public":
        base = root / "public"
        owner = None
    else:
        if not principal_id or any(part in principal_id for part in ("/", "\\", "..")):
            raise ValueError("invalid authenticated principal")
        base = root / "principals" / principal_id
        owner = principal_id
    return KnowledgeScope(
        scope=scope,
        principal_id=owner,
        documents_dir=base / "documents",
        index_path=base / "index.md",
        collection_name=collection_name(scope, owner),
    )


def add_resource(
    client: Any,
    target: KnowledgeScope,
    resource_id: str,
    display_name: str,
    content: str,
    embedding: list[float],
) -> None:
    target.documents_dir.mkdir(parents=True, exist_ok=True)
    (target.documents_dir / f"{resource_id}.md").write_text(content, encoding="utf-8")
    existing = target.index_path.read_text(encoding="utf-8") if target.index_path.exists() else ""
    target.index_path.write_text(
        existing + f"| LangGraph | {display_name} | {resource_id} |\n",
        encoding="utf-8",
    )
    collection = client.get_or_create_collection(target.collection_name)
    collection.add(
        ids=[f"{resource_id}:1:1"],
        embeddings=[embedding],
        documents=[content],
        metadatas=[
            {
                "resource_id": resource_id,
                "scope": target.scope,
                "owner_id": target.principal_id or "public",
                "display_name": display_name,
                "start_line": 1,
                "end_line": 1,
            }
        ],
    )


def ids(result: dict[str, Any]) -> list[str]:
    return list((result.get("ids") or [[]])[0])


def query_visible(
    client: Any,
    public: KnowledgeScope,
    personal: KnowledgeScope,
    embedding: list[float],
) -> set[str]:
    """分别查询公共和当前个人 collection，调用方不能传其他 collection。"""
    visible: set[str] = set()
    for target in (public, personal):
        collection = client.get_or_create_collection(target.collection_name)
        if collection.count() == 0:
            continue
        result = collection.query(query_embeddings=[embedding], n_results=10)
        visible.update(ids(result))
    return visible


def delete_resource(client: Any, target: KnowledgeScope, resource_id: str) -> None:
    """在一个可信作用域内同步删除文件、index 行和向量。"""
    (target.documents_dir / f"{resource_id}.md").unlink()
    lines = target.index_path.read_text(encoding="utf-8").splitlines()
    target.index_path.write_text(
        "\n".join(line for line in lines if resource_id not in line) + "\n",
        encoding="utf-8",
    )
    client.get_or_create_collection(target.collection_name).delete(
        where={"resource_id": resource_id}
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ia-phase7-scoped-index-") as tmp:
        root = Path(tmp)
        client = chromadb.PersistentClient(path=str(root / "chroma"))
        public = resolve_scope(root / "knowledge", "public", None)
        alice = resolve_scope(root / "knowledge", "personal", "guest-a")
        bob = resolve_scope(root / "knowledge", "personal", "guest-b")

        assert alice.collection_name != bob.collection_name
        assert "guest-a" not in alice.collection_name
        try:
            resolve_scope(root / "knowledge", "personal", "../guest-b")
        except ValueError:
            pass
        else:
            raise AssertionError("scope resolver accepted path traversal")

        add_resource(client, public, "public-guide", "公共指南", "公共知识", [1.0, 0.0])
        add_resource(client, alice, "alice-note", "个人笔记", "Alice 知识", [0.9, 0.1])
        add_resource(client, bob, "bob-note", "个人笔记", "Bob 知识", [0.8, 0.2])

        visible = query_visible(client, public, alice, [1.0, 0.0])
        assert visible == {"public-guide:1:1", "alice-note:1:1"}
        assert "bob-note:1:1" not in visible

        delete_resource(client, alice, "alice-note")
        assert not (alice.documents_dir / "alice-note.md").exists()
        assert "alice-note" not in alice.index_path.read_text(encoding="utf-8")
        assert client.get_collection(alice.collection_name).count() == 0
        assert client.get_collection(bob.collection_name).count() == 1
        assert client.get_collection(public.collection_name).count() == 1

        # Windows 清理临时 Chroma 前显式停止本地客户端。
        client._system.stop()

    print("PASS: 作用域解析拒绝路径穿越，collection 名稳定且不暴露 principal。")
    print("PASS: 公共 + 当前个人的联合向量召回不会包含其他游客资源。")
    print("PASS: 资源删除同步清理文件、index 行和个人向量，不影响其他作用域。")


if __name__ == "__main__":
    main()
