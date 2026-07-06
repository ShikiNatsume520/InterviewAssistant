"""简历优化子图的状态与数据结构定义。

Phase 5 的 ``ResumeState`` 是本子图的完整状态 schema，与 ``MainState`` 解耦。
输入层由 ``intent`` + ``original_resume`` 构成，运行期维护
``current_draft`` / ``last_draft`` + ``plan_steps`` 进度，输出靠 ``messages``
携带 ``ToolMessage`` 退出子图。
"""

from __future__ import annotations

from typing import Annotated, Literal

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class ResumeState(TypedDict, total=False):
    """简历优化子图的图状态。

    输入层（由主图 wrapper 注入）：
        intent: 用户修改简历的需求描述（主图 Router LLM 精炼后传入）。
        original_resume: 原始简历文本（wrapper 从主图最近 HumanMessage 提取）。

    运行期：
        current_draft: 当前草稿，CRUD 工具修改此字段。
        last_draft: 上一步快照，``step_confirm`` 拒绝时恢复。
        plan_steps: ``plan_node`` 产出的计划步骤列表。
        steps_completed: 已完成步骤数（注入 react_router 的 system prompt）。
        messages: ReAct 循环的 LLM / Tool 消息历史。
    """

    intent: str
    original_resume: str
    current_draft: str
    last_draft: str
    plan_steps: list[str]
    steps_completed: int
    messages: Annotated[list[BaseMessage], add_messages]


class PlanConfirmResult(TypedDict, total=False):
    """``plan_confirm_node`` interrupt 后用户回复的解析结构。"""

    decision: Literal["approve", "suggest"]
    suggestion: str
