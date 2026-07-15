"""phase16_inbound_normalize_probe — inbound resume 归一化 + schema 校验原型验证。

验证目标
--------
1. server 归一化层 ``_normalize_resume_value(phase, value)`` 能把**旧前端裸串/旧 dict**
   转成统一规范 dict ``{action, ...}``。
2. 新 React 前端发的规范 dict **原样透传**（幂等）。
3. 规范 dict 经 inbound schema 校验通过；非法形态被 schema 拒绝。

不在范围：实际跑图、实际 HTTP。仅纯函数 + Pydantic 校验。

运行：``uv run python prototypes/phase16_inbound_normalize_probe.py``
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

# ── inbound schema（与 contracts.py 待新增的对齐） ──────────────────────────


class ResumeSelectInbound(BaseModel):
    action: Literal["select"]
    resume_file: str = Field(default="", description="选定的简历文件名")
    resume_shot: str = Field(default="", description="简历全文（markdown 字符串）")


class DecisionInbound(BaseModel):
    """决策类 inbound：approve / reject / suggest（resume_approve / plan_confirm / outline_confirm 共用）。"""

    action: Literal["approve", "reject", "suggest"]
    suggestion: str = Field(default="", description="建议内容（suggest 时填）")
    selection: str = Field(default="", description="选区提示（resume 场景可选）")


class HitlInbound(BaseModel):
    action: Literal["new_request", "exit"]
    request: str = Field(default="", description="新修改意图（new_request 时填）")
    selection: str = Field(default="", description="选区提示（可选）")
    save: bool = Field(default=False, description="保存退出（exit 时填）")


class ConnectivityInbound(BaseModel):
    action: Literal["continue"]


PHASE_TO_INBOUND: dict[str, type[BaseModel]] = {
    "resume_select": ResumeSelectInbound,
    "resume_approve": DecisionInbound,
    "plan_confirm": DecisionInbound,
    "outline_confirm": DecisionInbound,
    "resume_hitl": HitlInbound,
    "connectivity_check": ConnectivityInbound,
}


# ── server 归一化层（与 app.py 待新增的对齐） ──────────────────────────────


def _normalize_resume_value(phase: str, value: Any) -> Any:
    """把旧前端裸串/旧 dict 转成统一规范 dict ``{action, ...}``。

    规范 dict 原样透传（幂等）；旧形态按 phase 归一化。
    """
    schema = PHASE_TO_INBOUND.get(phase)
    if schema is None:
        return value  # 未知 phase：不碰，透传（向后兼容）

    # ── resume_select：旧形态是 {resume_file, resume_shot}（无 action） ──
    if phase == "resume_select":
        if isinstance(value, dict):
            if value.get("action") == "select":
                return value
            return {"action": "select", "resume_file": str(value.get("resume_file", "")),
                    "resume_shot": str(value.get("resume_shot", ""))}
        return {"action": "select", "resume_file": "", "resume_shot": str(value)}

    # ── 决策类（resume_approve / plan_confirm / outline_confirm） ──
    if phase in ("resume_approve", "plan_confirm", "outline_confirm"):
        if isinstance(value, dict):
            # 旧 dict 用 decision 字段 → 映射到 action
            if "decision" in value and "action" not in value:
                return {"action": value["decision"],
                        "suggestion": str(value.get("suggestion", "")),
                        "selection": str(value.get("selection", ""))}
            return value  # 已是规范 dict（含 action）
        # 裸串 → action
        s = str(value).strip()
        if s in ("approve", "reject", "suggest"):
            return {"action": s}
        # outline_confirm 旧中文兜底
        if s in ("拒绝", "拒绝深研"):
            return {"action": "reject"}
        if phase == "outline_confirm" and s in ("continue",):
            return {"action": "approve"}  # 历史误用，approve 走通
        return {"action": "approve"}  # 默认 approve

    # ── resume_hitl ──
    if phase == "resume_hitl":
        if isinstance(value, dict):
            if "action" in value:
                return value
            # 旧形态已用 action，透传
            return value
        return {"action": "new_request", "request": str(value)}

    # ── connectivity_check ──
    if phase == "connectivity_check":
        return {"action": "continue"}

    return value


def _validate(phase: str, value: Any) -> dict[str, Any]:
    """归一化后用 schema 校验，返回 dict。"""
    schema = PHASE_TO_INBOUND.get(phase)
    if schema is None:
        return value if isinstance(value, dict) else {"raw": value}
    normalized = _normalize_resume_value(phase, value)
    return schema.model_validate(normalized).model_dump()


# ── 测试用例 ────────────────────────────────────────────────────────────────


def main() -> None:
    cases: list[tuple[str, str, Any, dict[str, Any]]] = [
        # (phase, 描述, 输入, 期望归一化后)
        # resume_select —— 旧形态（无 action）
        ("resume_select", "旧 select dict（无 action）",
         {"resume_file": "r.md", "resume_shot": "# 简历"},
         {"action": "select", "resume_file": "r.md", "resume_shot": "# 简历"}),
        # resume_select —— 新规范 dict 原样透传
        ("resume_select", "新 select dict（带 action）",
         {"action": "select", "resume_file": "r.md", "resume_shot": "# 简历"},
         {"action": "select", "resume_file": "r.md", "resume_shot": "# 简历"}),

        # plan_confirm —— 主页旧裸串 "approve"
        ("plan_confirm", "主页旧裸串 approve", "approve",
         {"action": "approve", "suggestion": "", "selection": ""}),
        # plan_confirm —— 主页旧 suggest dict（用 decision）
        ("plan_confirm", "主页旧 suggest dict（decision）",
         {"decision": "suggest", "suggestion": "多加项目细节"},
         {"action": "suggest", "suggestion": "多加项目细节", "selection": ""}),
        # plan_confirm —— resume 页旧 dict（decision: approve）
        ("plan_confirm", "resume 页旧 dict（decision approve）",
         {"decision": "approve"}, {"action": "approve", "suggestion": "", "selection": ""}),
        # plan_confirm —— 新规范 dict
        ("plan_confirm", "新规范 dict（action suggest）",
         {"action": "suggest", "suggestion": "x", "selection": "选区"},
         {"action": "suggest", "suggestion": "x", "selection": "选区"}),

        # outline_confirm —— 旧裸串 approve/reject
        ("outline_confirm", "旧裸串 approve", "approve",
         {"action": "approve", "suggestion": "", "selection": ""}),
        ("outline_confirm", "旧裸串 reject", "reject",
         {"action": "reject", "suggestion": "", "selection": ""}),
        ("outline_confirm", "旧中文兜底 拒绝深研", "拒绝深研",
         {"action": "reject", "suggestion": "", "selection": ""}),
        ("outline_confirm", "旧 suggest dict", {"decision": "suggest", "suggestion": "改大纲"},
         {"action": "suggest", "suggestion": "改大纲", "selection": ""}),

        # resume_approve —— resume 页旧 dict（decision）
        ("resume_approve", "旧 dict decision reject",
         {"decision": "reject"}, {"action": "reject", "suggestion": "", "selection": ""}),
        ("resume_approve", "新 dict action approve",
         {"action": "approve"}, {"action": "approve", "suggestion": "", "selection": ""}),

        # resume_hitl —— resume 页旧 dict（action，已规范）
        ("resume_hitl", "旧/新 hitl new_request",
         {"action": "new_request", "request": "再改教育", "selection": ""},
         {"action": "new_request", "request": "再改教育", "selection": "", "save": False}),
        ("resume_hitl", "旧/新 hitl exit save",
         {"action": "exit", "save": True},
         {"action": "exit", "request": "", "selection": "", "save": True}),

        # connectivity_check —— 旧裸串 continue
        ("connectivity_check", "旧裸串 continue", "continue", {"action": "continue"}),
    ]

    passed = 0
    failed = 0
    for phase, desc, inp, expected in cases:
        try:
            normalized = _normalize_resume_value(phase, inp)
            validated = _validate(phase, inp)
        except ValidationError as e:
            print(f"[FAIL] {phase} | {desc}\n  ValidationError: {e}")
            failed += 1
            continue
        # 归一化层只保证 action 字段正确（不补默认值——补全是 schema 的事）
        ok_norm = normalized.get("action") == expected.get("action")
        # validated 是 schema 校验后的最终值（传给图），必须与 expected 全字段一致
        ok_valid = validated == expected
        if ok_norm and ok_valid:
            print(f"[PASS] {phase} | {desc}")
            print(f"       in={inp!r}")
            print(f"       normalized={normalized}")
            print(f"       validated ={validated}")
            passed += 1
        else:
            print(f"[FAIL] {phase} | {desc}")
            print(f"  in={inp!r}")
            print(f"  expected   ={expected}")
            print(f"  normalized ={normalized}  ok={ok_norm}")
            print(f"  validated  ={validated}  ok={ok_valid}")
            failed += 1

    # 非法形态应被 schema 拒绝
    illegal_cases: list[tuple[str, str, Any]] = [
        ("plan_confirm", "非法 action", {"action": "foobar"}),
        ("outline_confirm", "缺 action 裸串乱写", "乱七八糟"),  # 归一化默认 approve，不拒
    ]
    print("\n--- 非法形态测试（应拒绝或安全兜底）---")
    for phase, desc, inp in illegal_cases:
        try:
            validated = _validate(phase, inp)
            print(f"[INFO] {phase} | {desc} → 归一化为 {validated}（未拒绝，安全兜底）")
        except ValidationError as e:
            print(f"[PASS] {phase} | {desc} → 正确拒绝：{str(e).splitlines()[0]}")

    print(f"\n=== 主用例：{passed} passed, {failed} failed ===")


if __name__ == "__main__":
    main()
