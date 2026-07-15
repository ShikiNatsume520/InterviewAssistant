"""phase10: resume_agent 常驻编辑会话子图机制原型。

验证阶段3核心图机制（stub chat_node 不调真 LLM，按 chat_modes 队列手设决策）：
1. chat_node 路由分发：grep_replace→edit_executor / request_plan→plan_node / 无tool_call→hitl_standby
2. approve_node 三选一 interrupt：approve→chat_node / reject→撤销→chat_node / suggest→撤销+建议→chat_node
3. plan_confirm 两选 interrupt：approve→chat_node / suggest→plan_node 重规划
4. hitl_standby interrupt：new_request(注入 HumanMessage)→chat_node / exit(设 save)→END
5. 完整常驻循环：finish→hitl→new_request→chat_node→edit→approve→chat_node→finish→hitl→exit

用 MemorySaver checkpointer + Command(resume=...) 驱动 interrupt。
路由用 conditional_edges + path_map（子图内单 LLM 决策点，无并行需求）。
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt


class _State(TypedDict, total=False):
    """resume 子图 state stub（阶段3 字段子集）。"""

    resume_shot: str
    last_shot: str
    save: bool
    plan: list[str]
    last_summary: str
    chat_step: int
    chat_modes: list[str]
    messages: Annotated[list[BaseMessage], add_messages]


# --------------------------------------------------------------------------- #
# stub chat_node：按 chat_modes 队列依次产出决策（模拟 LLM）
# --------------------------------------------------------------------------- #


def chat_node(state: _State) -> dict[str, Any]:
    step = state.get("chat_step", 0)
    modes = state.get("chat_modes", ["finish"])
    mode = modes[step] if step < len(modes) else "finish"
    if mode == "edit":
        msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "id": f"e{step}",
                    "name": "grep_replace",
                    "args": {"grep_target": "学校A", "replace_content": f"大学X{step}"},
                }
            ],
        )
        return {"messages": [msg], "chat_step": step + 1}
    if mode == "plan":
        msg = AIMessage(
            content="",
            tool_calls=[{"id": f"p{step}", "name": "request_plan", "args": {}}],
        )
        return {"messages": [msg], "chat_step": step + 1}
    # finish：无 tool_call，文本即总结
    return {
        "messages": [AIMessage(content="简历优化完成总结")],
        "last_summary": "简历优化完成总结",
        "chat_step": step + 1,
    }


def route_after_chat(state: _State) -> str:
    """据 messages[-1] 的 tool_calls 分发到三分支。"""
    msgs = state.get("messages", [])
    if not msgs:
        return "hitl_standby"
    last = msgs[-1]
    tcs = getattr(last, "tool_calls", []) or []
    if not tcs:
        return "hitl_standby"
    name = str(tcs[0].get("name", ""))
    if name == "request_plan":
        return "plan_node"
    if name == "grep_replace":
        return "edit_executor"
    return "hitl_standby"


# --------------------------------------------------------------------------- #
# plan 分支
# --------------------------------------------------------------------------- #


def plan_node(state: _State) -> dict[str, Any]:
    return {"plan": ["步骤1: 修改教育", "步骤2: 修改工作"]}


def plan_confirm_node(state: _State) -> dict[str, Any]:
    plan = state.get("plan", [])
    value = interrupt({"phase": "plan_confirm", "plan": plan})
    if isinstance(value, dict) and value.get("decision") == "suggest":
        suggestion = str(value.get("suggestion", ""))
        return {"plan": [], "messages": [HumanMessage(content=f"用户建议: {suggestion}")]}
    return {}  # approve


def route_after_plan_confirm(state: _State) -> str:
    if not state.get("plan"):
        return "plan_node"  # suggest → 重规划
    return "chat_node"  # approve


# --------------------------------------------------------------------------- #
# edit 分支
# --------------------------------------------------------------------------- #


def edit_executor_node(state: _State) -> dict[str, Any]:
    """串行执行 grep_replace 波 + 波前 last_shot 快照（内联最小版，逻辑同 phase9）。"""
    msgs = state.get("messages", [])
    ai = msgs[-1]
    draft = state.get("resume_shot", "")
    last_shot = draft  # 波前快照
    tcs = getattr(ai, "tool_calls", []) or []
    tool_msgs: list[ToolMessage] = []
    for tc in tcs:
        args = tc.get("args", {}) or {}
        target = str(args.get("grep_target", ""))
        rep = str(args.get("replace_content", ""))
        if target and draft.count(target) == 1:
            draft = draft.replace(target, rep)
            tool_msgs.append(
                ToolMessage(content="已替换", tool_call_id=str(tc.get("id", "")))
            )
        else:
            tool_msgs.append(
                ToolMessage(content="失败", tool_call_id=str(tc.get("id", "")))
            )
    return {"resume_shot": draft, "last_shot": last_shot, "messages": tool_msgs}


def approve_node(state: _State) -> dict[str, Any]:
    before = state.get("last_shot", "")
    after = state.get("resume_shot", "")
    value = interrupt({"phase": "approve", "before": before, "after": after})
    if isinstance(value, dict):
        decision = str(value.get("decision", "approve"))
        if decision == "reject":
            return {"resume_shot": state.get("last_shot", "")}  # 撤销整波
        if decision == "suggest":
            suggestion = str(value.get("suggestion", ""))
            return {
                "resume_shot": state.get("last_shot", ""),  # 撤销整波
                "messages": [HumanMessage(content=f"用户建议: {suggestion}")],
            }
    return {}  # approve


# --------------------------------------------------------------------------- #
# hitl 待命分支
# --------------------------------------------------------------------------- #


def hitl_standby_node(state: _State) -> dict[str, Any]:
    value = interrupt({"phase": "hitl_standby", "summary": state.get("last_summary", "")})
    if isinstance(value, dict) and value.get("action") == "exit":
        return {"save": bool(value.get("save", False))}
    # new_request：注入 HumanMessage，回 chat_node 让 LLM 看到新需求
    request = str(value.get("request", "")) if isinstance(value, dict) else str(value)
    return {"messages": [HumanMessage(content=request)]}


def route_after_hitl(state: _State) -> str:
    """据是否注入了 HumanMessage 区分：new_request→chat_node / exit→END。

    exit 路径不注入消息，messages[-1] 仍是 finish 的 AIMessage → END；
    new_request 路径注入 HumanMessage → messages[-1] 是 HumanMessage → chat_node。
    无需额外 flag，exit 终态无 staleness 问题。
    """
    msgs = state.get("messages", [])
    if msgs and isinstance(msgs[-1], HumanMessage):
        return "chat_node"
    return END


# --------------------------------------------------------------------------- #
# 构图 + 运行
# --------------------------------------------------------------------------- #


def build_graph() -> Any:
    wf = StateGraph(_State)
    wf.add_node("chat_node", chat_node)
    wf.add_node("plan_node", plan_node)
    wf.add_node("plan_confirm", plan_confirm_node)
    wf.add_node("edit_executor", edit_executor_node)
    wf.add_node("approve_node", approve_node)
    wf.add_node("hitl_standby", hitl_standby_node)

    wf.set_entry_point("chat_node")
    wf.add_conditional_edges(
        "chat_node",
        route_after_chat,
        {"plan_node": "plan_node", "edit_executor": "edit_executor", "hitl_standby": "hitl_standby"},
    )
    wf.add_edge("plan_node", "plan_confirm")
    wf.add_conditional_edges(
        "plan_confirm",
        route_after_plan_confirm,
        {"plan_node": "plan_node", "chat_node": "chat_node"},
    )
    wf.add_edge("edit_executor", "approve_node")
    wf.add_edge("approve_node", "chat_node")
    wf.add_conditional_edges(
        "hitl_standby",
        route_after_hitl,
        {"chat_node": "chat_node", END: END},
    )
    return wf.compile(checkpointer=MemorySaver())


def run_flow(
    graph: Any, initial: dict[str, Any], resumes: list[Any], tid: str = "t1"
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """运行图 + 按序 resume 各 interrupt，返回 (最终 state values, next 节点元组)。"""
    config: dict[str, Any] = {"configurable": {"thread_id": tid}}
    graph.invoke(initial, config)
    for r in resumes:
        graph.invoke(Command(resume=r), config)
    state = graph.get_state(config)
    return dict(state.values), state.next


def _msgs_contain(values: dict[str, Any], needle: str) -> bool:
    return any(needle in str(getattr(m, "content", "")) for m in values.get("messages", []))


def main() -> None:
    g = build_graph()
    draft = "## 教育经历\n学校A\n## 工作经历\n公司B\n"

    # 1. edit → approve(approve) → finish → hitl → exit(save=True)
    vals, nxt = run_flow(
        g,
        {"resume_shot": draft, "last_shot": "", "chat_modes": ["edit", "finish"]},
        [{"decision": "approve"}, {"action": "exit", "save": True}],
        tid="s1",
    )
    assert nxt == (), nxt
    assert "大学X0" in vals["resume_shot"], vals["resume_shot"]
    assert vals["save"] is True
    print("[1] edit→approve→finish→exit(save): OK")

    # 2. edit → approve(reject) → finish → hitl → exit  撤销整波
    vals, nxt = run_flow(
        g,
        {"resume_shot": draft, "last_shot": "", "chat_modes": ["edit", "finish"]},
        [{"decision": "reject"}, {"action": "exit", "save": False}],
        tid="s2",
    )
    assert nxt == ()
    assert vals["resume_shot"] == draft, "reject 应回滚到改前"
    assert vals["save"] is False
    print("[2] edit→approve(reject)撤销整波: OK")

    # 3. edit → approve(suggest) → finish → hitl → exit  撤销+注入建议
    vals, nxt = run_flow(
        g,
        {"resume_shot": draft, "last_shot": "", "chat_modes": ["edit", "finish"]},
        [{"decision": "suggest", "suggestion": "再改详细点"}, {"action": "exit", "save": False}],
        tid="s3",
    )
    assert nxt == ()
    assert vals["resume_shot"] == draft, "suggest 应回滚"
    assert _msgs_contain(vals, "再改详细点"), "suggest 应注入建议消息"
    print("[3] edit→approve(suggest)撤销+注入建议: OK")

    # 4. plan → plan_confirm(approve) → finish → hitl → exit
    vals, nxt = run_flow(
        g,
        {"resume_shot": draft, "last_shot": "", "chat_modes": ["plan", "finish"]},
        [{"decision": "approve"}, {"action": "exit", "save": True}],
        tid="s4",
    )
    assert nxt == ()
    assert vals["plan"] == ["步骤1: 修改教育", "步骤2: 修改工作"], vals.get("plan")
    print("[4] plan→plan_confirm(approve)→chat: OK")

    # 5. plan → plan_confirm(suggest) → plan_node → plan_confirm(approve) → finish → exit
    vals, nxt = run_flow(
        g,
        {"resume_shot": draft, "last_shot": "", "chat_modes": ["plan", "finish"]},
        [
            {"decision": "suggest", "suggestion": "加个技能章节"},
            {"decision": "approve"},
            {"action": "exit", "save": True},
        ],
        tid="s5",
    )
    assert nxt == ()
    assert vals["plan"] == ["步骤1: 修改教育", "步骤2: 修改工作"]
    assert _msgs_contain(vals, "加个技能章节")
    print("[5] plan_confirm(suggest)→重规划→approve: OK")

    # 6. 完整常驻循环：finish→hitl(new_request)→edit→approve→finish→hitl(exit)
    vals, nxt = run_flow(
        g,
        {"resume_shot": draft, "last_shot": "", "chat_modes": ["finish", "edit", "finish"]},
        [
            {"action": "new_request", "request": "把学校A改成大学X"},
            {"decision": "approve"},
            {"action": "exit", "save": True},
        ],
        tid="s6",
    )
    assert nxt == (), nxt
    assert "大学X1" in vals["resume_shot"], vals["resume_shot"]  # edit 在 step1
    assert vals["save"] is True
    assert _msgs_contain(vals, "把学校A改成大学X"), "new_request 应注入 HumanMessage"
    print("[6] 常驻循环 new_request→edit→approve→exit: OK")

    print("\nALL PASSED")


if __name__ == "__main__":
    main()
