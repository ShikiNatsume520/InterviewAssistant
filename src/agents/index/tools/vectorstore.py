"""Chroma 向量库访问工具。

负责 Chroma collection 的 upsert/查询。chunk 的行级 metadata
（file_path/start_line/end_line/heading）随向量一并入库。

embedding 函数与 ``COLLECTION_NAME`` 常量已上提到 ``kernel.embedder``（R0 重构），
本模块仅保留 collection 读写操作。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents.index.state import Chunk, chunk_id, chunk_to_metadata
from kernel.embedder import COLLECTION_NAME, get_embed_fn

if TYPE_CHECKING:
    import chromadb
    from chromadb import Collection
    from chromadb.api.types import QueryResult


def get_collection(
    client: chromadb.api.ClientAPI, name: str = COLLECTION_NAME
) -> Collection:
    """获取或创建 Chroma collection（cosine 空间 + 绑定的 embedding 函数）。

    Args:
        client: chromadb 客户端（PersistentClient 等）。
        name: collection 名，默认 ``COLLECTION_NAME``。

    Returns:
        配置好 embedding_function 与 cosine 空间的 Collection 对象。
    """
    return client.get_or_create_collection(
        name,
        embedding_function=get_embed_fn(),
        metadata={"hnsw:space": "cosine"},
    )


def upsert_chunks(
    client: chromadb.api.ClientAPI,
    chunks: list[Chunk],
    name: str = COLLECTION_NAME,
) -> int:
    """把切片向量化并 upsert 进 Chroma collection（按 chunk_id 去重覆盖）。

    Args:
        client: chromadb 客户端。
        chunks: 待入库的 Chunk 列表。

    Returns:
        实际写入的向量条数。
    """
    if not chunks:
        return 0
    col = get_collection(client, name)
    col.upsert(
        ids=[chunk_id(c) for c in chunks],
        documents=[c["content"] for c in chunks],
        metadatas=[chunk_to_metadata(c) for c in chunks],
    )
    return len(chunks)


def delete_resource_chunks(
    client: chromadb.api.ClientAPI, resource_id: str, name: str = COLLECTION_NAME
) -> None:
    """按稳定 resource_id 删除一个知识资源的全部向量切片。"""
    get_collection(client, name).delete(where={"resource_id": resource_id})


def delete_file_chunks(
    client: chromadb.api.ClientAPI, file_path: str, name: str = COLLECTION_NAME
) -> None:
    """兼容删除重构前没有 resource_id metadata 的公共文件向量。"""
    get_collection(client, name).delete(where={"file_path": file_path})


def query_chunks(
    client: chromadb.api.ClientAPI, query: str, n_results: int = 5
) -> list[Chunk]:
    """对查询文本做向量检索，返回 top-k chunk（带行号 metadata）。

    Args:
        client: chromadb 客户端。
        query: 查询文本。
        n_results: 召回条数，默认 5。

    Returns:
        命中的 Chunk 列表（按相关度降序），含行号字段。
    """
    col = get_collection(client)
    res: QueryResult = col.query(query_texts=[query], n_results=n_results)
    ids = (res["ids"] or [[]])[0]
    metas = (res["metadatas"] or [[]])[0]
    docs = (res["documents"] or [[]])[0]
    out: list[Chunk] = []
    for i in range(len(ids)):
        meta: dict[str, Any] = dict(metas[i])
        out.append(
            {
                "file_path": meta["file_path"],
                "start_line": int(meta["start_line"]),
                "end_line": int(meta["end_line"]),
                "heading": meta["heading"],
                "content": docs[i],
            }
        )
    return out


def make_persistent_client(db_path: str | Path) -> chromadb.api.ClientAPI:
    """创建指向本地目录的 Chroma 持久化客户端。

    Args:
        db_path: 向量库持久化目录（如 data/chroma）。

    Returns:
        chromadb PersistentClient。
    """
    import chromadb

    return chromadb.PersistentClient(path=str(db_path))
