"""Index Agent 的状态与数据结构定义。

本模块定义 Index Agent（后台 agent，不进主图流程）在图节点间传递的 State，
以及索引维护所需的 Pydantic 数据结构（LLM 工具调用的输入/输出 schema）。

设计要点：
- Index Agent 的输入是「显式待处理文件清单」（target_files），不扫描全目录。
- chunk 的 metadata 严格保留 start_line/end_line/file_path（行级引用硬约束）。
- 索引文件 data/index.md 是文件级语义索引（指向文件，不指行），由 LLM 维护。
"""

from __future__ import annotations

from typing import Any, TypedDict

from pydantic import BaseModel, Field


class Chunk(TypedDict):
    """单个 markdown 切片。

    Attributes:
        file_path: 所属 markdown 文件名（不含目录前缀，如 langgraph_state.md）。
        start_line: 该 chunk 在原文中的起始行号（1-based，闭区间）。
        end_line: 该 chunk 在原文中的结束行号（1-based，闭区间）。
        heading: 该 chunk 所属的 markdown 标题文本（最近一级标题）。
        content: 该 chunk 的纯文本内容。
    """

    file_path: str
    start_line: int
    end_line: int
    heading: str
    content: str


class IndexRow(BaseModel):
    """单条索引项（LLM 工具调用的输出单元）。

    LLM 维护索引时，对每个语义主题输出一条 IndexRow：关键词 + 摘要 + 指向的文件名。
    后端拿到 files 后拼接标准引用 [文件名](data/markdown/文件名)（文件级，无行号）。
    LLM 不碰引用格式、不碰行号，只产语义内容。

    Attributes:
        keywords: 该索引项的核心关键词列表。
        summary: 该索引项的摘要文本（不超过 SUMMARY_MAX 字符）。
        files: 该索引项指向的 markdown 文件名列表（不含路径）。
    """

    keywords: list[str] = Field(description="该索引项的核心关键词")
    summary: str = Field(description="该索引项的摘要，精炼不超过 100 字符")
    files: list[str] = Field(description="该索引项指向的 markdown 文件名列表（不含路径）")


class IndexUpdate(BaseModel):
    """LLM 工具调用的完整输出：更新后的完整索引表。

    LLM 看「现有索引 + 新文件 chunks」后，判断合并/新建，输出更新后的完整索引表
    （含未变动行原样保留）。后端用此结果覆盖写回 data/index.md。

    Attributes:
        rows: 更新后的完整索引行列表。
    """

    rows: list[IndexRow] = Field(description="更新后的完整索引表")


class IndexAgentState(TypedDict, total=False):
    """Index Agent 的图状态。

    各节点按需读写以下字段（total=False 表示字段均可选，按节点职责填充）。

    Attributes:
        target_files: 输入层——显式待处理的 markdown 文件名列表（scan_node 读取）。
        existing_rows: scan_node 读到的现有索引行（无 index.md 时为空列表）。
        chunks: chunk_node 产出的切片列表（带行号 metadata）。
        index_update: llm_index_node 产出的更新后索引表。
    """

    target_files: list[str]
    existing_rows: list[IndexRow]
    chunks: list[Chunk]
    index_update: IndexUpdate


def chunk_to_metadata(chunk: Chunk) -> dict[str, Any]:
    """提取 chunk 的行级引用 metadata（供 Chroma 向量灌入时附带）。

    Args:
        chunk: 单个切片。

    Returns:
        含 file_path/start_line/end_line/heading 的 metadata dict。
    """
    return {
        "file_path": chunk["file_path"],
        "start_line": chunk["start_line"],
        "end_line": chunk["end_line"],
        "heading": chunk["heading"],
    }


def chunk_id(chunk: Chunk) -> str:
    """为 chunk 生成稳定的唯一 id（用于 Chroma upsert 去重）。

    Args:
        chunk: 单个切片。

    Returns:
        形如 ``file_path:start_line:end_line`` 的 id 字符串。
    """
    return f"{chunk['file_path']}:{chunk['start_line']}:{chunk['end_line']}"
