"""RAG 子图的状态与数据结构定义。

Phase 2 的 RAGState 是本子图的完整状态 schema，不与 MainState 耦合。
输入层由 search_query + search_type 构成，输出层为 citations_output + gap_topic。
"""

from __future__ import annotations

from typing import Literal, TypedDict


class RawResult(TypedDict):
    """单条检索命中的原始数据结构。

    Attributes:
        file_path: 文件名（不含目录）。
        start_line: 起始行号（1-based）。
        end_line: 结束行号（1-based）。
        heading: 所属 markdown 标题。
        content: 命中行区间的内容。
        score: 归一化后的相关分（余弦距离 0~2 → 1/(1+dist) 映射到 0~1）。
        source: 来源管道，``"vector"`` 或 ``"grep"``。
    """

    file_path: str
    start_line: int
    end_line: int
    heading: str
    content: str
    score: float
    source: str


class Citation(TypedDict):
    """最终输出的结构化引用。

    Attributes:
        file_path: 文件名（不含目录）。
        start_line: 起始行号（1-based）。
        end_line: 结束行号（1-based）。
        content: 引用内容。
    """

    file_path: str
    start_line: int
    end_line: int
    content: str


class RAGState(TypedDict, total=False):
    """RAG 子图的图状态。

    输入层（由主图注入，Phase 3 之前可手工构造输入）：
        search_query: 主图 Router LLM 精炼后的检索词。
        search_type: 检索管道选择，``"semantic"``（语义）或 ``"keyword"``（关键词）。

    内部：
        raw_results: 管道产出的原始结果列表（retrieve_node 写入）。

    输出层：
        citations_output: 排序后的结构化引用列表（aggregate_node 写入）。
        gap_topic: 知识缺口主题（全管道无有效命中时写入）。
    """

    search_query: str
    search_type: Literal["semantic", "keyword"]
    raw_results: list[RawResult]
    citations_output: list[Citation]
    gap_topic: str | None
