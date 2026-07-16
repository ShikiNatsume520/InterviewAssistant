"""inbound resume 归一化 + schema 校验单测。

覆盖 ``normalize_resume_value`` 把兼容输入归一化为规范 ``{action, ...}``
dict，以及 ``PHASE_TO_INBOUND`` schema 校验。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from kernel.contracts import (
    PHASE_TO_INBOUND,
    normalize_resume_value,
)
from server import app as server_app


def _normalize_and_validate(phase: str, value: object) -> dict[str, Any]:
    """复刻 server ``_normalize_and_validate``：归一化 + schema 校验。"""
    normalized = normalize_resume_value(phase, value)
    schema = PHASE_TO_INBOUND[phase]
    return schema.model_validate(normalized).model_dump()


def test_resume_interrupt_product_payload_contains_recoverable_workspace() -> None:
    payload = server_app._interrupt_payload(
        [
            SimpleNamespace(
                value={
                    "phase": "resume_approve",
                    "edit_id": "edit-1",
                    "ordinal": 1,
                    "total": 2,
                    "section": "项目经历",
                    "reason": "突出架构能力",
                    "before_text": "旧描述",
                    "after_text": "新描述",
                    "before": "# 项目\n旧描述",
                    "after": "# 项目\n新描述",
                    "edits": [],
                }
            )
        ],
        {
            "resumeId": "resume-1",
            "displayName": "Agent 简历",
            "draft": "# 项目\n旧描述",
        },
    )
    interrupt = payload["interrupts"][0]
    assert interrupt["edit_id"] == "edit-1"
    assert interrupt["workspace"]["resumeId"] == "resume-1"
    assert interrupt["workspace"]["draft"] == "# 项目\n旧描述"


# ── 决策类：裸串 / 旧 {decision} / 新 {action} ────────────────────────────


@pytest.mark.parametrize("phase", ["resume_approve", "plan_confirm", "outline_confirm"])
def test_decision_legacy_bare_string_approve(phase: str) -> None:
    """旧主页裸串 approve → {action: approve}。"""
    out = _normalize_and_validate(phase, "approve")
    assert out["action"] == "approve"


def test_outline_confirm_legacy_bare_string_reject() -> None:
    """旧主页裸串 reject → {action: reject}。"""
    out = _normalize_and_validate("outline_confirm", "reject")
    assert out["action"] == "reject"


def test_outline_confirm_legacy_chinese_reject() -> None:
    """旧主页中文兜底「拒绝深研」→ {action: reject}。"""
    out = _normalize_and_validate("outline_confirm", "拒绝深研")
    assert out["action"] == "reject"


def test_decision_legacy_decision_dict_suggest() -> None:
    """旧 dict {decision: suggest, suggestion} → 映射到 action。"""
    out = _normalize_and_validate(
        "plan_confirm",
        {"decision": "suggest", "suggestion": "多加项目细节"},
    )
    assert out == {
        "action": "suggest",
        "suggestion": "多加项目细节",
        "selection": "",
    }


def test_decision_new_action_dict_approve() -> None:
    """新 React 规范 dict {action: approve} → 原样透传。"""
    out = _normalize_and_validate("resume_approve", {"action": "approve"})
    assert out == {
        "action": "approve",
        "suggestion": "",
        "selection": "",
    }


def test_decision_new_action_dict_suggest_with_selection() -> None:
    """新 React 规范 suggest 带 selection → 透传。"""
    out = _normalize_and_validate(
        "resume_approve",
        {"action": "suggest", "suggestion": "改这段", "selection": "选区"},
    )
    assert out["action"] == "suggest"
    assert out["suggestion"] == "改这段"
    assert out["selection"] == "选区"


# ── resume_hitl：旧/新都已用 action，透传 ─────────────────────────────────


def test_hitl_new_request() -> None:
    out = _normalize_and_validate(
        "resume_hitl",
        {"action": "new_request", "request": "再改教育"},
    )
    assert out == {
        "action": "new_request",
        "request": "再改教育",
        "selection": "",
        "save": False,
    }


def test_hitl_exit_save() -> None:
    out = _normalize_and_validate("resume_hitl", {"action": "exit", "save": True})
    assert out["action"] == "exit"
    assert out["save"] is True


# ── connectivity_check：裸串 continue → {action: continue} ────────────────


def test_connectivity_legacy_bare_continue() -> None:
    out = _normalize_and_validate("connectivity_check", "continue")
    assert out == {"action": "continue"}


def test_connectivity_new_dict() -> None:
    out = _normalize_and_validate("connectivity_check", {"action": "continue"})
    assert out == {"action": "continue"}


# ── schema 校验：非法 action 被拒 ────────────────────────────────────────


def test_illegal_action_rejected() -> None:
    """非法 action 值应被 schema 拒绝。"""
    with pytest.raises(ValidationError):
        _normalize_and_validate("plan_confirm", {"action": "foobar"})


def test_hitl_illegal_action_rejected() -> None:
    """hitl 的 action 非 new_request/exit 应被拒。"""
    with pytest.raises(ValidationError):
        _normalize_and_validate("resume_hitl", {"action": "approve"})


# ── 未知 phase：不碰，原样透传（向后兼容） ────────────────────────────────


def test_unknown_phase_passthrough() -> None:
    """未知 phase（未来新增 interrupt）→ 归一化层不碰，原样返回。"""
    out = normalize_resume_value("future_phase", {"whatever": 1})
    assert out == {"whatever": 1}
