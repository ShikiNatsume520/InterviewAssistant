"""Research Agent 的独立图状态。"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

ResearchSourceStatus = Literal[
    "pending",
    "fetching",
    "fetched",
    "distilling",
    "succeeded",
    "failed",
    "skipped",
]
ResearchPhase = Literal[
    "planning",
    "waiting_plan",
    "checking_network",
    "searching",
    "fetching",
    "distilling",
    "composing_report",
    "waiting_knowledge_decision",
    "completed",
    "aborted",
]


class ResearchSource(TypedDict):
    """一个网页来源在研究流水线中的可恢复状态。"""

    source_id: str
    query: str
    url: str
    title: str
    status: ResearchSourceStatus
    failure_reason: str
    note: str


class ResearchState(TypedDict, total=False):
    """Research 子图输入、运行状态和结构化输出。"""

    gap_topic: str
    principal_id: str
    thread_id: str
    tool_call_id: str
    outline: list[str]
    outline_feedback: str
    connect_attempts: int
    connectivity_ok: bool
    _pending_interrupt: dict[str, Any]
    query_cursor: int
    source_cursor: int
    sources: list[ResearchSource]
    current_raw_content: str
    phase: ResearchPhase
    report_markdown: str
    report_summary: str
    proposed_file_name: str
    knowledge_decision: Literal["pending", "approved", "rejected", "not_asked"]
    import_status: Literal["pending", "completed", "failed", "not_requested"]
    knowledge_resource_id: str
    approval_status: Literal["approved", "rejected", "aborted"]
