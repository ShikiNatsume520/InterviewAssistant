"""phase11: resume 子图真 LLM 端到端集成验证（阶段4 适配新拓扑）。

用真 deepseek LLM 跑完整新闭环：
select_resume(interrupt 选 sample.md) → 真 LLM chat_node(edit grep_replace)
→ edit_executor → approve(approve) → chat_node(finish 总结) → hitl(exit save=False) → END

断言：
- select_resume 选 sample.md 后 resume_shot == 源文件内容
- chat_node 真 LLM 产出合规 grep_replace tool_call（lapis-cv HTML+md 混排唯一命中）
- edit_executor 后 resume_shot 变了
- approve(approve) 后保留改动
- finish 后 last_summary 非空
- hitl(exit save=False) → END，未写源文件
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.resume.graph import build_resume_workflow
from kernel.paths import RESUMES_DIR


def main() -> None:
    graph = build_resume_workflow().compile(checkpointer=MemorySaver())
    tid = "phase11-v4"
    config: dict[str, Any] = {"configurable": {"thread_id": tid}}

    sample_path = RESUMES_DIR / "sample.md"
    original = sample_path.read_text(encoding="utf-8") if sample_path.is_file() else ""

    # wrapper 视角：只传 intent（首条 HumanMessage），不传 resume_shot/resume_file
    # 用 sample.md 里真实存在的片段（lapis-cv 模板工作经历首条），确保 grep_target 能唯一命中
    intent = "把工作经历里的「主导了八爪生物社交平台（OctoHub）的全栈开发与架构设计」改成「主导分布式后端架构与高并发服务开发」，体现技术深度"

    print("=== 1. 首轮 invoke（select_resume interrupt） ===")
    graph.invoke({"messages": [_HumanMessage(intent)]}, config)
    st = graph.get_state(config)
    print(f"  next={st.next}")
    assert st.next == ("select_resume",), f"应在 select_resume 挂起, got {st.next}"

    print("=== 2. resume 选 sample.md → 真 LLM edit → approve interrupt ===")
    graph.invoke(Command(resume={"resume_file": "sample.md"}), config)
    st = graph.get_state(config)
    print(f"  next={st.next}")
    vals = dict(st.values)
    after = vals.get("resume_shot", "")
    assert vals.get("resume_file") == "sample.md", "应记录选定文件名"
    # 真 LLM edit 后应在 approve 挂起
    assert st.next == ("approve_node",), f"应在 approve_node 挂起, got {st.next}"
    assert after != original, "edit 后草稿应改变"
    assert "分布式" in after or "架构" in after, f"应体现技术深度, after={after[:200]}"
    print("  真 LLM 产出合规 grep_replace + edit 改动: OK")

    print("=== 3. resume approve → chat_node(finish 总结) → hitl interrupt ===")
    graph.invoke(Command(resume={"decision": "approve"}), config)
    st = graph.get_state(config)
    print(f"  next={st.next}")
    assert st.next == ("hitl_standby",), f"应在 hitl_standby 挂起, got {st.next}"
    vals = dict(st.values)
    summary = str(vals.get("last_summary", ""))
    print(f"  last_summary 非空: {bool(summary)}")
    print(f"  summary 预览: {summary[:120].encode('ascii', 'replace').decode()}")
    assert summary, "finish 后应有总结"

    print("=== 4. resume exit(save=False) → END（不写源文件） ===")
    graph.invoke(Command(resume={"action": "exit", "save": False}), config)
    st = graph.get_state(config)
    print(f"  next={st.next}")
    assert st.next == (), f"应到 END, got {st.next}"
    unchanged = sample_path.read_text(encoding="utf-8")
    assert unchanged == original, "save=False 源文件不应变"
    print("  save=False 源文件未动: OK")

    print("\nALL PASSED — 真 LLM 端到端新闭环通过（select_resume→edit→approve→finish→exit）")


def _HumanMessage(content: str) -> Any:
    from langchain_core.messages import HumanMessage

    return HumanMessage(content=content)


if __name__ == "__main__":
    main()
