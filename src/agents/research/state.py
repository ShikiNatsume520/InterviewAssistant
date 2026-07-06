"""research_agent 子图的状态与数据结构定义。

Phase 6 的 ``ResearchState`` 是本子图的完整状态 schema，与 ``MainState`` 解耦。
输入层由 ``gap_topic`` 构成，运行期维护连通性重试计数与累积的搜索笔记，输出靠
``approval_status`` / ``summary`` / ``new_file_name`` 反馈给主图。

连通性重试机制（原型 phase6_subgraph_interrupt_probe.py 实测）:
    ``interrupt()`` resume 后节点从头重跑, 重跑到 ``interrupt()`` 那行时不再阻塞,
    返回 resume 值并继续执行其后代码。故 ``connect_attempts`` 累计写在 ``interrupt()``
    **之后**的 return 里（POST-INTERRUPT）, 由条件边据 attempts 路由。
"""

from __future__ import annotations

from typing import Literal, TypedDict


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
        connect_attempts: 连通性检查失败累计次数（POST-INTERRUPT 写入, 3 次后 abort）。
        connectivity_ok: 最近一次连通性检查结果。
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
    research_notes: list[ResearchNote]
    new_file_name: str
    approval_status: Literal["approved", "rejected", "aborted"]
    summary: str
