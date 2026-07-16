"""阶段 4 探针：验证最小双向交接契约不依赖共享消息历史。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


@dataclass(frozen=True)
class ResumeTaskBrief:
    request: str
    target_role: str | None
    constraints: list[str]
    relevant_context: list[str]


@dataclass(frozen=True)
class ResumeTaskResult:
    outcome: Literal["saved", "discarded"]
    summary: str
    resume_file: str
    final_draft: str

    def validate(self) -> None:
        if self.outcome == "discarded" and self.final_draft:
            raise ValueError("discarded result must not expose the abandoned draft")


def main() -> None:
    brief = ResumeTaskBrief(
        request="把多智能体项目写进简历",
        target_role="后端 Agent 工程师",
        constraints=["避免夸张表达", "突出可验证的工程工作"],
        relevant_context=["实现 Main、Resume、Research 多智能体协作"],
    )
    assert "messages" not in asdict(brief)
    assert "resume" not in asdict(brief)

    saved = ResumeTaskResult(
        outcome="saved",
        summary="已补充多智能体项目经历。",
        resume_file="resume.md",
        final_draft="# 简历\n",
    )
    saved.validate()

    discarded = ResumeTaskResult(
        outcome="discarded",
        summary="用户放弃本次简历修改。",
        resume_file="resume.md",
        final_draft="",
    )
    discarded.validate()
    print("phase 4 handoff contract probe: PASS")


if __name__ == "__main__":
    main()
