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

from typing import Any, Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# 子图发出的 interrupt payload
# --------------------------------------------------------------------------- #


class PlanConfirmPayload(BaseModel):
    """``resume_agent`` 的 ``plan_confirm_node`` interrupt payload。"""

    phase: Literal["plan_confirm"] = "plan_confirm"
    plan: list[str] = Field(default_factory=list, description="计划步骤列表")


class GrepReplaceItem(BaseModel):
    """单个 ``grep_replace`` tool_call 的 grep_target/replace_content 对。

    供前端在 approve 时标红（grep_target=将删）、标绿（replace_content=将增）。
    """

    grep_target: str = Field(default="", description="要定位并替换的原文片段（标红）")
    replace_content: str = Field(default="", description="替换的新内容（标绿）")


class ResumeApprovePayload(BaseModel):
    """``resume_agent`` 常驻会话 ``approve_node`` interrupt payload。"""

    phase: Literal["resume_approve"] = "resume_approve"
    before: str = Field(default="", description="修改波前草稿")
    after: str = Field(default="", description="修改波后草稿")
    edits: list[GrepReplaceItem] = Field(
        default_factory=list,
        description="本波 grep_replace 的 target/replace 对，供前端标红绿",
    )


class ResumeHitlPayload(BaseModel):
    """``resume_agent`` 常驻会话 ``hitl_standby_node`` interrupt payload。"""

    phase: Literal["resume_hitl"] = "resume_hitl"
    summary: str = Field(default="", description="chat_node 产出的本次会话总结")


class ResumeSelectPayload(BaseModel):
    """``resume_agent`` 常驻会话 ``select_resume_node`` interrupt payload。

    后端驱动范式：select_resume 仅挂起等前端回传 ``{resume_file, resume_shot}``，
    **不扫不读文件**——前端用 webkitdirectory 扫本地目录读文件。payload 最小化，
    仅带 ``intent``（主图精炼的 query）供主页跳转 /resume 时走 URL 传前端作右栏
    首条消息。chat_node 文本不进 payload（流式 token 实时推前端）。
    """

    phase: Literal["resume_select"] = "resume_select"
    intent: str = Field(default="", description="主图精炼的修改 query，带出给主页")


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
# 用户回复的 resume 值（inbound）
# --------------------------------------------------------------------------- #
#
# 统一约定：所有 inbound resume 值为 ``{action: <枚举>, ...附带字段}`` dict。
# server 在 ``Command(resume=...)`` 前用 ``normalize_resume_value`` 把旧前端裸串 /
# 旧 dict（``{decision: ...}`` / ``{resume_file, resume_shot}``）归一化成规范 dict，
# 再用本节 schema 校验。子图节点只读规范字段，不再做 ``isinstance`` 兜底。
#
# 设计选 ``action`` 而非 ``decision``：``action`` 能覆盖 new_request / exit / continue /
# select 等非"决策"语义（hitl 已在用），全 phase 统一最自然。


class ResumeSelectInbound(BaseModel):
    """``resume_select`` 的 inbound：前端选简历文件后回传。

    旧前端发 ``{resume_file, resume_shot}``（无 ``action``），server 归一化补
    ``action: "select"``。
    """

    action: Literal["select"]
    resume_file: str = Field(default="", description="选定的简历文件名")
    resume_shot: str = Field(default="", description="简历全文（markdown 字符串）")


class DecisionInbound(BaseModel):
    """决策类 inbound：``approve`` / ``reject`` / ``suggest``。

    ``resume_approve`` / ``plan_confirm`` / ``outline_confirm`` 共用。``plan_confirm``
    无 reject（计划只能批准或建议），但 schema 统一允许 reject，由路由层忽略——
    保持单一 schema 减少分支。
    """

    action: Literal["approve", "reject", "suggest"]
    suggestion: str = Field(default="", description="建议内容（suggest 时填）")
    selection: str = Field(default="", description="选区提示（resume 场景可选）")


class HitlInbound(BaseModel):
    """``resume_hitl`` 的 inbound：常驻会话待命时的用户信号。"""

    action: Literal["new_request", "exit"]
    request: str = Field(default="", description="新修改意图（new_request 时填）")
    selection: str = Field(default="", description="选区提示（可选）")
    save: bool = Field(default=False, description="保存退出（exit 时填）")


class ConnectivityInbound(BaseModel):
    """``connectivity_check`` 的 inbound：用户挂梯子后继续。"""

    action: Literal["continue"]


PHASE_TO_INBOUND: dict[str, type[BaseModel]] = {
    "resume_select": ResumeSelectInbound,
    "resume_approve": DecisionInbound,
    "plan_confirm": DecisionInbound,
    "outline_confirm": DecisionInbound,
    "resume_hitl": HitlInbound,
    "connectivity_check": ConnectivityInbound,
}
"""inbound phase → schema 映射。server 归一化 + 校验、节点解析共用。"""


def normalize_resume_value(phase: str, value: Any) -> Any:
    """把旧前端裸串 / 旧 dict 归一化成统一规范 dict ``{action, ...}``。

    规范 dict（含 ``action``）原样透传（幂等）；旧形态按 phase 转换。未知 phase
    不碰，原样返回（向后兼容未来新增的 interrupt）。归一化后由调用方用
    ``PHASE_TO_INBOUND[phase]`` 校验。

    Args:
        phase: 当前 pending interrupt 的 ``phase``（由 server 查 state 得到）。
        value: 前端经 ``resume_value`` 回传的原始值。

    Returns:
        规范 dict（或未知 phase 时原样 value）。
    """
    if phase not in PHASE_TO_INBOUND:
        return value

    # resume_select：旧形态 {resume_file, resume_shot}（无 action）
    if phase == "resume_select":
        if isinstance(value, dict):
            if value.get("action") == "select":
                return value
            return {
                "action": "select",
                "resume_file": str(value.get("resume_file", "")),
                "resume_shot": str(value.get("resume_shot", "")),
            }
        return {"action": "select", "resume_file": "", "resume_shot": str(value)}

    # 决策类：resume_approve / plan_confirm / outline_confirm
    if phase in ("resume_approve", "plan_confirm", "outline_confirm"):
        if isinstance(value, dict):
            # 旧 dict 用 decision 字段 → 映射到 action
            if "decision" in value and "action" not in value:
                return {
                    "action": value["decision"],
                    "suggestion": str(value.get("suggestion", "")),
                    "selection": str(value.get("selection", "")),
                }
            return value  # 已是规范 dict（含 action）
        s = str(value).strip()
        if s in ("approve", "reject", "suggest"):
            return {"action": s}
        # outline_confirm 旧中文兜底
        if s in ("拒绝", "拒绝深研"):
            return {"action": "reject"}
        return {"action": "approve"}  # 默认 approve（保持旧行为）

    # resume_hitl：旧/新形态都已用 action
    if phase == "resume_hitl":
        if isinstance(value, dict):
            return value
        return {"action": "new_request", "request": str(value)}

    # connectivity_check：旧裸串 continue → {action: continue}
    if phase == "connectivity_check":
        return {"action": "continue"}

    return value


# 旧契约保留：``approve`` / ``reject`` 裸字符串解析已由 ``normalize_resume_value``
# 统一处理，``SuggestDecision`` 仅为兼容历史引用保留。
class SuggestDecision(BaseModel):
    """用户 resume 值：建议场景（已废弃，由 ``DecisionInbound`` 取代）。

    ``normalize_resume_value`` 把 ``{decision: "suggest", suggestion}`` 旧形态统一
    映射为 ``{action: "suggest", suggestion}``。保留此类仅为兼容历史引用。
    """

    decision: Literal["suggest"]
    suggestion: str = Field(default="", description="用户建议内容")
