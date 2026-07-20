"""阶段 4B 探针：结构化交接、活动模式与幂等简历保存。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ResumeTaskBrief:
    resume_id: str
    user_request: str | None = None


@dataclass(frozen=True)
class ResumeTaskResult:
    source_resume_id: str
    output_resume_id: str | None
    outcome: Literal["saved", "discarded"]
    summary: str
    display_name: str


class FakeResumeRepository:
    def __init__(self) -> None:
        self.documents = {
            "resume-1": {
                "owner": "alice",
                "name": "Agent 后端岗位简历",
                "content": "# 项目\n旧描述",
                "writes": 0,
            }
        }
        self.derivations: dict[str, str] = {}

    def require(self, owner: str, resume_id: str) -> dict[str, object]:
        document = self.documents.get(resume_id)
        if document is None or document["owner"] != owner:
            raise LookupError("resume not found")
        return document

    def create_derived(
        self, owner: str, source_id: str, content: str, derivation_key: str
    ) -> str:
        source = self.require(owner, source_id)
        existing = self.derivations.get(derivation_key)
        if existing is not None:
            return existing
        output_id = f"derived-{len(self.derivations) + 1}"
        self.documents[output_id] = {
            "owner": owner,
            "name": f"{source['name']}_优化版",
            "content": content,
            "writes": 1,
        }
        self.derivations[derivation_key] = output_id
        return output_id


def main() -> None:
    repository = FakeResumeRepository()
    brief = ResumeTaskBrief(
        resume_id="resume-1",
        user_request="优化多智能体项目经历",
    )
    minimal_brief = ResumeTaskBrief(resume_id="resume-1")
    assert minimal_brief.user_request is None

    thread = {"active_mode": "chat", "active_agent": "main"}
    document = repository.require("alice", "resume-1")
    resume_state = {
        "resume_id": "resume-1",
        "resume_shot": document["content"],
        "task_brief": brief,
    }
    thread.update(active_mode="resume", active_agent="resume")
    assert resume_state["resume_shot"] == "# 项目\n旧描述"

    # 会话级退出操作只在 standby 暴露；计划确认和逐条审批不能同时退出。
    allowed_actions = {
        "plan_confirm": {"approve", "suggest"},
        "approve_node": {"approve", "reject", "suggest"},
        "hitl_standby": {"new_request", "save", "discard"},
    }
    assert "save" not in allowed_actions["plan_confirm"]
    assert "discard" not in allowed_actions["approve_node"]
    assert {"save", "discard"} <= allowed_actions["hitl_standby"]

    try:
        repository.require("bob", "resume-1")
    except LookupError:
        pass
    else:
        raise AssertionError("cross-principal handoff must fail")

    final_draft = "# 项目\n基于 LangGraph 构建多智能体面试助手"
    output_id = repository.create_derived(
        "alice", "resume-1", final_draft, "thread-1:tool-call-1"
    )
    repeated_id = repository.create_derived(
        "alice", "resume-1", final_draft, "thread-1:tool-call-1"
    )
    assert repeated_id == output_id
    assert repository.documents["resume-1"]["content"] == "# 项目\n旧描述"

    result = ResumeTaskResult(
        source_resume_id="resume-1",
        output_resume_id=output_id,
        outcome="saved",
        summary="已优化多智能体项目经历。",
        display_name="Agent 后端岗位简历",
    )
    assert not hasattr(result, "final_draft")
    discarded = ResumeTaskResult(
        source_resume_id="resume-1",
        output_resume_id=None,
        outcome="discarded",
        summary="用户放弃本轮工作草稿。",
        display_name="Agent 后端岗位简历",
    )
    assert discarded.output_resume_id is None
    thread.update(active_mode="chat", active_agent="main")
    assert thread == {"active_mode": "chat", "active_agent": "main"}
    print("phase 4B handoff persistence probe: PASS")


if __name__ == "__main__":
    main()
