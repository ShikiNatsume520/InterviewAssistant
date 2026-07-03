"""Chroma 向量库访问工具。

负责 embedding 函数构造、Chroma collection 的 upsert/查询。
chunk 的行级 metadata（file_path/start_line/end_line/heading）随向量一并入库。

实现说明：
- embedding 函数通过 ``SiliconFlowEmbeddingFunction`` 调用硅基流动 OpenAI 兼容
  embedding API（.env 中 ``SILICONFLOW_*`` 配置），在线推理、支持中文语义检索。
- embedding 函数做模块级单例缓存，避免重复创建 API client。
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

_embed_fn: EmbeddingFunction[Any] | None = None
"""模块级 embedding 函数单例（懒加载，避免重复创建 API client）。"""


class SiliconFlowEmbeddingFunction:
    """Chroma EmbeddingFunction 适配层：调用硅基流动 OpenAI 兼容 embedding API。

    环境变量:
        SILICONFLOW_API_KEY — API 密钥
        SILICONFLOW_BASE_URL — API 基地址，默认 https://api.siliconflow.cn/v1
        SILICONFLOW_EMBEDDING_MODEL — 模型名，如 BAAI/bge-large-zh-v1.5
    """

    def __init__(self) -> None:
        import os

        from openai import OpenAI

        self._model = os.environ["SILICONFLOW_EMBEDDING_MODEL"]
        base_url = os.environ.get(
            "SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"
        )
        api_key = os.environ["SILICONFLOW_API_KEY"]
        self._client = OpenAI(base_url=base_url, api_key=api_key)

    def __call__(self, input: list[str]) -> list[list[float]]:
        """编码文本列表，返回按 input 顺序的 1024 维向量列表。"""
        response = self._client.embeddings.create(
            model=self._model, input=input
        )
        ordered = sorted(response.data, key=lambda x: x.index)
        return [item.embedding for item in ordered]

    def embed_query(self, input: list[str]) -> list[list[float]]:
        """编码 query 列表（Chroma 调用，直接委派 ``__call__``）。"""
        return self.__call__(input)

    def embed_documents(self, input: list[str]) -> list[list[float]]:
        """批量编码文档（Chroma 索引/补全路径调用）。"""
        return self.__call__(input)

    def name(self) -> str:
        """返回模型名（Chromac_get_or_create_collection 内部校验需要）。"""
        return self._model


def get_embed_fn() -> EmbeddingFunction[Any]:
    """获取（惰性创建并缓存的）Chroma embedding 函数单例。

    Returns:
        SiliconFlowEmbeddingFunction 实例（绑定远程 API）。
    """
    global _embed_fn
    if _embed_fn is None:
        _embed_fn = SiliconFlowEmbeddingFunction()
    return _embed_fn


def get_collection(
    client: chromadb.api.ClientAPI, name: str = COLLECTION_NAME
) -> Collection:
    """获取或创建 Chroma collection（cosine 空间 + 绑定的 embedding 函数）。

    Args:
        client: chromadb 客户端（PersistentClient 等）。
        name: collection 名，默认 knowledge_base。

    Returns:
        配置好 embedding_function 与 cosine 空间的 Collection 对象。
    """
    return client.get_or_create_collection(
        name,
        embedding_function=get_embed_fn(),
        metadata={"hnsw:space": "cosine"},
    )


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
