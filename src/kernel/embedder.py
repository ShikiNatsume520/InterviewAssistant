"""Chroma embedding 函数与 collection 常量（跨 agent 共享）。

从 ``index_agent/tools/vectorstore.py`` 抽出（R0 上提到 kernel）。
``COLLECTION_NAME`` 在此唯一定义，``rag_agent`` 与 ``index_agent`` 共享，
消除两处重复定义。

实现说明：
- embedding 函数通过 ``SiliconFlowEmbeddingFunction`` 调用硅基流动 OpenAI 兼容
  embedding API（.env 中 ``SILICONFLOW_*`` 配置），在线推理、支持中文语义检索。
- embedding 函数做模块级单例缓存，避免重复创建 API client。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chromadb.api.types import EmbeddingFunction

COLLECTION_NAME: str = "knowledge_base"
"""Chroma 单一 collection 名（领域知识与面经不分类，统一存储；rag/index 共享）。"""

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
        """从环境变量构造 SiliconFlow embedding 客户端。"""
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
        response = self._client.embeddings.create(model=self._model, input=input)
        ordered = sorted(response.data, key=lambda x: x.index)
        return [item.embedding for item in ordered]

    def embed_query(self, input: list[str]) -> list[list[float]]:
        """编码 query 列表（Chroma 调用，直接委派 ``__call__``）。"""
        return self.__call__(input)

    def embed_documents(self, input: list[str]) -> list[list[float]]:
        """批量编码文档（Chroma 索引/补全路径调用）。"""
        return self.__call__(input)

    def name(self) -> str:
        """返回模型名（Chroma get_or_create_collection 内部校验需要）。"""
        return self._model


def get_embed_fn() -> EmbeddingFunction[Any]:
    """获取（惰性创建并缓存的）Chroma embedding 函数单例。

    Returns:
        SiliconFlowEmbeddingFunction 实例（绑定远程 API）。
    """
    global _embed_fn
    if _embed_fn is None:
        _embed_fn = SiliconFlowEmbeddingFunction()  # type: ignore[assignment]
    assert _embed_fn is not None
    return _embed_fn
