"""Chroma 向量库访问工具。

负责 embedding 函数构造、Chroma collection 的 upsert/查询。
chunk 的行级 metadata（file_path/start_line/end_line/heading）随向量一并入库。

实现说明：
- 使用 Chroma 官方 SentenceTransformerEmbeddingFunction，model_name 指向本地
  ``models/bge-small-zh-v1.5`` 目录，离线加载、GPU 自动启用。
- embedding 函数做模块级单例缓存，避免每次访问 collection 都重载模型。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from index_agent.state import Chunk, chunk_id, chunk_to_metadata

if TYPE_CHECKING:
    import chromadb
    from chromadb import Collection
    from chromadb.api.types import EmbeddingFunction, QueryResult

COLLECTION_NAME = "knowledge_base"
"""Chroma 单一 collection 名（领域知识与面经不分类，统一存储）。"""

DEFAULT_MODEL_PATH = "models/bge-small-zh-v1.5"
"""本地 bge embedding 模型路径（相对项目根）。"""

_embed_fn: EmbeddingFunction[Any] | None = None
"""模块级 embedding 函数单例（懒加载，避免重复重载模型）。"""


def get_embed_fn() -> EmbeddingFunction[Any]:
    """获取（惰性创建并缓存的）Chroma embedding 函数单例。

    Returns:
        指向本地 bge 模型的 SentenceTransformerEmbeddingFunction。
    """
    global _embed_fn
    if _embed_fn is None:
        from chromadb.utils.embedding_functions import (
            SentenceTransformerEmbeddingFunction,
        )

        _embed_fn = SentenceTransformerEmbeddingFunction(model_name=DEFAULT_MODEL_PATH)
    return _embed_fn


def get_collection(
    client: chromadb.api.ClientAPI, name: str = COLLECTION_NAME
) -> Collection:
    """获取或创建 Chroma collection（绑定本地 embedding 函数）。

    Args:
        client: chromadb 客户端（PersistentClient 等）。
        name: collection 名，默认 knowledge_base。

    Returns:
        配置好 embedding_function 的 Collection 对象。
    """
    return client.get_or_create_collection(name, embedding_function=get_embed_fn())


def upsert_chunks(client: chromadb.api.ClientAPI, chunks: list[Chunk]) -> int:
    """把切片向量化并 upsert 进 Chroma collection（按 chunk_id 去重覆盖）。

    Args:
        client: chromadb 客户端。
        chunks: 待入库的 Chunk 列表。

    Returns:
        实际写入的向量条数。
    """
    if not chunks:
        return 0
    col = get_collection(client)
    col.upsert(
        ids=[chunk_id(c) for c in chunks],
        documents=[c["content"] for c in chunks],
        metadatas=[chunk_to_metadata(c) for c in chunks],
    )
    return len(chunks)


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
