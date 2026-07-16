"""阶段 5 探针：产品事件驱动的可恢复 Resume 工作区。"""

from __future__ import annotations


def latest_pending_interrupt(events: list[dict[str, object]]) -> dict[str, object] | None:
    pending: dict[str, object] | None = None
    for event in events:
        event_type = event.get("type")
        if event_type == "interrupt.requested":
            pending = event
        elif event_type == "interrupt.resolved":
            pending = None
    return pending


def main() -> None:
    workspace = {
        "resumeId": "resume-1",
        "displayName": "Agent 简历",
        "draft": "# 项目\n旧描述",
    }
    approve = {
        "phase": "resume_approve",
        "editId": "tool-call-2",
        "ordinal": 2,
        "total": 4,
        "section": "项目经历",
        "reason": "突出多智能体架构",
        "beforeText": "旧描述",
        "afterText": "基于 LangGraph 构建多智能体系统",
        "workspace": workspace,
    }
    events: list[dict[str, object]] = [
        {"type": "interrupt.requested", "payload": approve}
    ]
    pending = latest_pending_interrupt(events)
    assert pending is not None
    assert pending["payload"] == approve

    # 刷新只重放持久产品事件，也能恢复草稿、当前 Diff 和稳定 edit ID。
    restored = latest_pending_interrupt(list(events))
    assert restored is not None
    restored_payload = restored["payload"]
    assert isinstance(restored_payload, dict)
    assert restored_payload["editId"] == "tool-call-2"

    events.append(
        {
            "type": "interrupt.resolved",
            "payload": {"phase": "resume_approve", "action": "approve"},
        }
    )
    assert latest_pending_interrupt(events) is None

    standby = {
        "phase": "resume_hitl",
        "summary": "本轮修改完成",
        "workspace": {**workspace, "draft": "# 项目\n新描述"},
        "actions": ["new_request", "save", "discard"],
    }
    events.append({"type": "interrupt.requested", "payload": standby})
    assert latest_pending_interrupt(events) is not None
    assert "save" not in approve.get("actions", [])
    assert "save" in standby["actions"]
    print("phase 5 interrupt contract probe: PASS")


if __name__ == "__main__":
    main()
