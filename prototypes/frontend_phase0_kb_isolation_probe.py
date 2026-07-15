"""阶段 0 探针 4：验证 Chroma 游客知识库隔离机制。

使用临时目录和显式二维 embedding，不调用在线 embedding 服务。验证：

1. 共享 collection + 强制 scope/owner filter；
2. 每游客独立 collection；
3. 同名文件/同片段序号不会互相覆盖；
4. 查询、删除和公共资料联合检索不会越权。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import chromadb

GUEST_A = "guest-a"
GUEST_B = "guest-b"


def ids(result: dict[str, Any]) -> set[str]:
    """提取 Chroma query 第一组结果 ID。"""
    rows = result.get("ids", [[]])
    return set(rows[0] if rows else [])


def shared_collection_probe(root: Path) -> None:
    """验证共享 collection 的强制 metadata filter。"""
    client = chromadb.PersistentClient(path=str(root / "shared"))
    collection = client.get_or_create_collection("knowledge_base")
    collection.add(
        ids=[
            "public::guide.md::0",
            f"{GUEST_A}::resume.md::0",
            f"{GUEST_B}::resume.md::0",
        ],
        documents=["公共 LangGraph 指南", "游客 A 私有简历", "游客 B 私有简历"],
        embeddings=[[1.0, 0.0], [0.9, 0.1], [0.8, 0.2]],
        metadatas=[
            {"scope": "public", "owner_id": "public", "file": "guide.md"},
            {"scope": "personal", "owner_id": GUEST_A, "file": "resume.md"},
            {"scope": "personal", "owner_id": GUEST_B, "file": "resume.md"},
        ],
    )

    guest_a_only = collection.query(
        query_embeddings=[[1.0, 0.0]],
        n_results=10,
        where={"owner_id": GUEST_A},
    )
    guest_b_only = collection.query(
        query_embeddings=[[1.0, 0.0]],
        n_results=10,
        where={"owner_id": GUEST_B},
    )
    guest_a_with_public = collection.query(
        query_embeddings=[[1.0, 0.0]],
        n_results=10,
        where={
            "$or": [
                {"scope": "public"},
                {"$and": [{"scope": "personal"}, {"owner_id": GUEST_A}]},
            ]
        },
    )

    assert ids(guest_a_only) == {f"{GUEST_A}::resume.md::0"}
    assert ids(guest_b_only) == {f"{GUEST_B}::resume.md::0"}
    assert ids(guest_a_with_public) == {
        "public::guide.md::0",
        f"{GUEST_A}::resume.md::0",
    }
    assert f"{GUEST_B}::resume.md::0" not in ids(guest_a_with_public)

    collection.delete(where={"owner_id": GUEST_A})
    remaining = set(collection.get()["ids"])
    assert remaining == {
        "public::guide.md::0",
        f"{GUEST_B}::resume.md::0",
    }
    print("PASS: 共享 collection 在强制 owner/scope filter 下查询和删除隔离正确。")


def separate_collection_probe(root: Path) -> None:
    """验证每游客独立 collection。"""
    client = chromadb.PersistentClient(path=str(root / "separate"))
    public = client.get_or_create_collection("kb_public")
    guest_a = client.get_or_create_collection("kb_guest_a")
    guest_b = client.get_or_create_collection("kb_guest_b")

    public.add(
        ids=["guide.md::0"],
        documents=["公共 LangGraph 指南"],
        embeddings=[[1.0, 0.0]],
    )
    guest_a.add(
        ids=["resume.md::0"],
        documents=["游客 A 私有简历"],
        embeddings=[[0.9, 0.1]],
    )
    guest_b.add(
        ids=["resume.md::0"],
        documents=["游客 B 私有简历"],
        embeddings=[[0.8, 0.2]],
    )

    assert set(guest_a.get()["documents"]) == {"游客 A 私有简历"}
    assert set(guest_b.get()["documents"]) == {"游客 B 私有简历"}
    assert set(public.get()["documents"]) == {"公共 LangGraph 指南"}

    guest_a.delete(ids=["resume.md::0"])
    assert guest_a.count() == 0
    assert guest_b.count() == 1
    assert public.count() == 1
    print("PASS: 独立 collection 允许同 ID，并在查询和删除时物理隔离。")


def main() -> None:
    """在临时持久化目录执行两种隔离探针。"""
    with tempfile.TemporaryDirectory(prefix="ia-phase0-kb-") as tmp:
        root = Path(tmp)
        shared_collection_probe(root)
        separate_collection_probe(root)

    print("\n=== 机制结论 ===")
    print("PASS: 两种 Chroma 隔离方式在机制上均可行。")
    print("NOTE: 共享 collection 的安全性依赖每次查询/删除都强制 owner filter。")
    print("NOTE: 独立 collection 默认隔离更强，但公共+个人检索需要查询两库后合并。")


if __name__ == "__main__":
    main()
