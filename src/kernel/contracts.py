"""主图↔子图↔前端 HITL interrupt payload 契约（Pydantic schema）。

R2 重构：把散落在 ``server.py`` + 3 个子图 + 前端的 interrupt payload 形状固化为
Pydantic schema，改协议有编译期保障。序列化字段名与重构前兼容（前端
``static/index.html`` 不改即可解析）。

两类契约：
- **子图发出的 interrupt payload**（``PlanConfirmPayload`` 等）：子图 ``interrupt()``
  构造、``server`` 序列化给前端时共用。
- **用户回复的 resume 值**：``"approve"`` / ``"reject"`` 裸字符串，或
  ``SuggestDecision``（``{"decision": "suggest", "suggestion": "..."}``）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# 子图发出的 interrupt payload
# --------------------------------------------------------------------------- #


class PlanConfirmPayload(BaseModel):
    """``resume_agent`` 的 ``plan_confirm_node`` interrupt payload。"""

    phase: Literal["plan_confirm"] = "plan_confirm"
    plan: list[str] = Field(default_factory=list, description="计划步骤列表")
    draft: str = Field(default="", description="当前简历草稿")


class StepConfirmPayload(BaseModel):
    """``resume_agent`` 的 ``step_confirm_node`` interrupt payload。"""

    phase: Literal["step_confirm"] = "step_confirm"
    before: str = Field(default="", description="修改前草稿")
    after: str = Field(default="", description="修改后草稿")


class OutlineConfirmPayload(BaseModel):
    """``research_agent`` 的 ``outline_confirm_node`` interrupt payload。"""

    phase: Literal["outline_confirm"] = "outline_confirm"
    gap_topic: str = Field(default="", description="知识缺口主题")
    outline: list[str] = Field(default_factory=list, description="检索词大纲")


class ConnectivityCheckPayload(BaseModel):
    """``research_agent`` 的 ``connectivity_check_node`` interrupt payload。"""

    phase: Literal["connectivity_check"] = "connectivity_check"
    attempts: int = Field(default=0, description="连通性失败累计次数")
    msg: str = Field(default="", description="提示文案")


# --------------------------------------------------------------------------- #
# 用户回复的 resume 值
# --------------------------------------------------------------------------- #


class SuggestDecision(BaseModel):
    """用户 resume 值：建议场景。

    ``approve`` / ``reject`` 是裸字符串（非 dict），server 直接按 ``str`` 解析；
    仅 ``suggest`` 走本 schema。
    """

    decision: Literal["suggest"]
    suggestion: str = Field(default="", description="用户建议内容")
