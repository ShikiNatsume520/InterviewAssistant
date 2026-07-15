"""research_agent 子图的状态与数据结构定义。

Phase 6 的 ``ResearchState`` 是本子图的完整状态 schema，与 ``MainState`` 解耦。
输入层由 ``gap_topic`` 构成，运行期维护连通性重试计数与累积的搜索笔记，输出靠
``approval_status`` / ``summary`` / ``new_file_name`` 反馈给主图。

连通性重试机制:
    ``connect_attempts`` **在 interrupt 之前**累计写入（PRE-INTERRUPT），随 checkpoint
    持久化。POST-INTERRUPT 写会丢（resume 读的是挂起前快照），导致 attempts 永远从
    初始值开始 → 死循环无法 abort。故用 ``_pending_interrupt`` 传 interrupt payload，
    ``connectivity_interrupt_node`` 单独负责挂起，``connectivity_check_node`` 只写累计 +
    payload，不 interrupt。
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict


class ResearchNote(TypedDict):
    """单条深研笔记（一次 URL 爬取 + LLM 提炼后的产物）。

    Attributes:
        query: 产生本笔记的检索词（outline 中的一个）。
        source_title: 来源网页标题。
        source_url: 来源网页 URL。
        content: LLM 提炼后的结构化笔记正文。
    """

    query: str
    source_title: str
    source_url: str
    content: str


class ResearchState(TypedDict, total=False):
    """research_agent 子图的图状态。

    输入层（由主图 wrapper 注入）:
        gap_topic: RAG 子图判定的知识缺口主题。

    运行期:
        outline: ``outline_node`` 产出的 3-5 个检索词列表。
        outline_feedback: ``outline_confirm_node`` 在 suggest 场景回填的用户建议
            （供 outline_node 重新规划时参考）。
        connect_attempts: 连通性检查失败累计次数（PRE-INTERRUPT 写入, 3 次后 abort）。
        connectivity_ok: 最近一次连通性检查结果。
        _pending_interrupt: connectivity_check 失败时传给 connectivity_interrupt_node
            的挂起 payload（ConnectivityCheckPayload dict）。PRE-INTERRUPT 写, 保证
            attempts 随 checkpoint 持久化, resume 后路由可读到正确累计值。
        research_notes: ``search_node`` 累积的笔记列表。

    输出层:
        new_file_name: ``finalize_node`` 写入 ``data/markdown/`` 的新文件名。
        approval_status: approved / rejected / aborted（rejected=用户拒大纲, aborted=网络 3 次失败）。
        summary: 深研知识总结（approved 时填充, 供主图反馈给 chat_node）。
    """

    gap_topic: str
    outline: list[str]
    outline_feedback: str
    connect_attempts: int
    connectivity_ok: bool
    _pending_interrupt: dict[str, Any]
    research_notes: list[ResearchNote]
    new_file_name: str
    approval_status: Literal["approved", "rejected", "aborted"]
    summary: str
