"""主图状态定义（简化版 — 不留子图 I/O 槽）。

与子智能体的通信通过标准 ``ToolMessage`` 完成，不在 ``MainState`` 中
为每个子图预留输入/输出槽位。
"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from agents.rag.state import Citation


def merge_current_turn_citations(
    current: list[Citation], update: list[Citation]
) -> list[Citation]:
    """清空新轮次候选，或合并同一轮并行 RAG 的候选资料。"""
    if not update:
        return []
    merged: dict[tuple[str, int, int], Citation] = {
        (item.get("resource_id", item["file_path"]), item["start_line"], item["end_line"]): item
        for item in current
    }
    for item in update:
        key = (
            item.get("resource_id", item["file_path"]),
            item["start_line"],
            item["end_line"],
        )
        previous = merged.get(key)
        if previous is None or item["score"] > previous["score"]:
            merged[key] = item
    return list(merged.values())


class MainState(TypedDict, total=False):
    """主图状态。

    Attributes:
        messages: 对话消息历史（``add_messages`` 累积）。
        citations: 最后一次 RAG 返回的结构化候选资料，供服务端校验 Main Agent
            声明的实际引用；前端只消费据此生成的 citation.list 产品事件。
        user_id: 当前会话的用户标识，用于 Store 键控（Phase 4）。
        current_resume: resume_agent 子图退出时回填的最终简历草稿（Phase 5）。
        research_output: research_agent 子图退出时回填的深研结果（approval_status 等，Phase 6）。
    """

    messages: Annotated[list[BaseMessage], add_messages]
    citations: Annotated[list[Citation], merge_current_turn_citations]
    user_id: str
    current_resume: str
    research_output: dict[str, Any]
