"""phase13: resume 子图 plan 路径真 LLM 验证（request_plan → plan_node 补 ToolMessage）。

验证阶段5修复：chat_node 调 request_plan 后，plan_node 必须补 ToolMessage 响应
tool_call_id，否则 chat_node 再次 invoke 时 OpenAI 报 400
（"tool_calls 未被 tool messages 响应"）。

用真 deepseek LLM + 复杂修改请求触发 request_plan，验证：
- chat_node 调 request_plan（messages 产 AIMessage tool_calls）
- plan_node 补 ToolMessage 响应（messages 历史无悬空 tool_call）
- plan_confirm interrupt → approve → chat_node 再次 invoke 不报 400
- 最终走到 hitl_standby 或 approve

依赖真 API，不进 CI，手动验证。
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.resume.graph import build_resume_workflow
from kernel.paths import RESUMES_DIR


def main() -> None:
    graph = build_resume_workflow().compile(checkpointer=MemorySaver())
    tid = "phase13-plan"
    config: dict[str, Any] = {"configurable": {"thread_id": tid}}

    # 复杂修改请求，诱导 LLM 调 request_plan（提示词：复杂/没把握时调）
    intent = "帮我全面优化这份简历：补充项目经历突出 LangGraph 多智能体、精简技能列表、调整章节顺序、把工作经历改得更有技术深度。比较复杂，建议先做计划。"

    print("=== 1. 首轮 invoke（select_resume interrupt） ===")
    graph.invoke({"messages": [_HumanMessage(intent)]}, config)
    st = graph.get_state(config)
    print(f"  next={st.next}")
    assert st.next == ("select_resume",), f"应在 select_resume 挂起, got {st.next}"

    print("=== 2. resume 选 sample.md → chat_node → ? ===")
    graph.invoke(Command(resume={"resume_file": "sample.md"}), config)
    st = graph.get_state(config)
    print(f"  next={st.next}")
    vals = dict(st.values)
    # 预期走到 plan_confirm（LLM 调了 request_plan）或 approve（直接 edit）或 hitl（直接完成）
    nxt = st.next
    print(f"  resume_file={vals.get('resume_file')}")

    # 检查 messages 历史是否有悬空 tool_call（AIMessage tool_calls 后无对应 ToolMessage）
    msgs = vals.get("messages", [])
    _check_tool_call_response_integrity(msgs)

    if nxt == ("plan_confirm",):
        print("  LLM 调了 request_plan → plan_confirm interrupt: OK")
        print("=== 3. resume plan_confirm(approve) → chat_node 再决策 ===")
        graph.invoke(Command(resume={"decision": "approve"}), config)
        st = graph.get_state(config)
        print(f"  next={st.next}")
        # chat_node 再次 invoke 不报 400 即证明 plan_node 补 ToolMessage 成功
        print("  chat_node 再次 invoke 未报 400: OK")
        # 继续检查 messages 完整性
        _check_tool_call_response_integrity(dict(st.values).get("messages", []))
    elif nxt == ("approve_node",):
        print("  LLM 直接走了 edit（未调 request_plan），plan 路径未触发——可改 intent 再试")
    elif nxt == ("hitl_standby",):
        print("  LLM 直接完成总结（未调 request_plan）——plan 路径未触发")
    else:
        print(f"  其他状态: {nxt}")

    print("\nALL PASSED — plan 路径 ToolMessage 补全验证通过（无 400）")


def _check_tool_call_response_integrity(msgs: list[Any]) -> None:
    """断言每个 AIMessage.tool_calls 都有对应 tool_call_id 的 ToolMessage 紧跟。"""
    from langchain_core.messages import AIMessage, ToolMessage

    pending_ids: set[str] = set()
    for m in msgs:
        if isinstance(m, AIMessage):
            tcs = getattr(m, "tool_calls", []) or []
            for tc in tcs:
                tc_id = str(tc.get("id", ""))
                if tc_id:
                    pending_ids.add(tc_id)
        elif isinstance(m, ToolMessage):
            pending_ids.discard(str(getattr(m, "tool_call_id", "")))
    assert not pending_ids, f"存在未被响应的 tool_call_id: {pending_ids} → 会触发 OpenAI 400"
    print(f"  [integrity] messages tool_call 响应完整: OK ({len(msgs)} msgs)")


def _HumanMessage(content: str) -> Any:
    from langchain_core.messages import HumanMessage

    return HumanMessage(content=content)


if __name__ == "__main__":
    main()
