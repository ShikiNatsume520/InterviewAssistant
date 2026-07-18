"""用真实 LangGraph Interrupt 验证 request_plan 的完整 ToolMessage 协议。"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict


class ProbeState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    plan: list[str]
    plan_status: Literal["none", "draft", "approved"]
    plan_feedback: str
    plan_request_tool_call_id: str
    edit_executed: bool


def chat_node(state: ProbeState) -> dict[str, Any]:
    messages = state.get("messages", [])
    if not messages:
        return {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "request_plan", "args": {}, "id": "plan-call-1"}
                    ],
                )
            ]
        }
    last = messages[-1]
    if isinstance(last, ToolMessage):
        result = json.loads(str(last.content))
        assert result["status"] == "approved"
        return {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "grep_replace",
                            "args": {"grep_target": "旧描述", "replace_content": "新描述"},
                            "id": "edit-call-1",
                        }
                    ],
                )
            ]
        }
    raise AssertionError("chat_node 未收到已批准的计划 ToolMessage")


def route_after_chat(state: ProbeState) -> str:
    last = state["messages"][-1]
    calls = getattr(last, "tool_calls", []) or []
    name = str(calls[0]["name"]) if calls else ""
    return {"request_plan": "plan", "grep_replace": "edit"}.get(name, "end")


def plan_node(state: ProbeState) -> dict[str, Any]:
    feedback = state.get("plan_feedback", "")
    plan = ["根据用户建议强化项目结果"] if feedback else ["强化项目架构描述"]
    latest = state["messages"][-1]
    call_id = str(getattr(latest, "tool_calls", [])[0]["id"])
    return {
        "plan": plan,
        "plan_status": "draft",
        "plan_request_tool_call_id": call_id,
        "plan_feedback": "",
    }


def plan_confirm_node(state: ProbeState) -> dict[str, Any]:
    value = interrupt({"phase": "plan_confirm", "plan": state.get("plan", [])})
    action = str(value.get("action", "")) if isinstance(value, dict) else ""
    if action == "suggest":
        return {
            "plan": [],
            "plan_status": "draft",
            "plan_feedback": str(value.get("suggestion", "")),
        }
    assert action == "approve"
    return {"plan_status": "approved"}


def route_after_confirm(state: ProbeState) -> str:
    return "result" if state.get("plan_status") == "approved" else "plan"


def plan_result_node(state: ProbeState) -> dict[str, Any]:
    return {
        "messages": [
            ToolMessage(
                tool_call_id=state["plan_request_tool_call_id"],
                content=json.dumps(
                    {
                        "status": "approved",
                        "plan": state["plan"],
                        "instruction": "计划已由用户批准，不要再次询问，立即执行。",
                    },
                    ensure_ascii=False,
                ),
            )
        ]
    }


def edit_node(state: ProbeState) -> dict[str, Any]:
    call = getattr(state["messages"][-1], "tool_calls", [])[0]
    return {
        "messages": [
            ToolMessage(tool_call_id=str(call["id"]), content="编辑已进入审批流程")
        ],
        "edit_executed": True,
    }


def build_graph() -> Any:
    workflow = StateGraph(ProbeState)
    workflow.add_node("chat", chat_node)
    workflow.add_node("plan", plan_node)
    workflow.add_node("confirm", plan_confirm_node)
    workflow.add_node("result", plan_result_node)
    workflow.add_node("edit", edit_node)
    workflow.set_entry_point("chat")
    workflow.add_conditional_edges(
        "chat", route_after_chat, {"plan": "plan", "edit": "edit", "end": END}
    )
    workflow.add_edge("plan", "confirm")
    workflow.add_conditional_edges(
        "confirm", route_after_confirm, {"plan": "plan", "result": "result"}
    )
    workflow.add_edge("result", "chat")
    workflow.add_edge("edit", END)
    return workflow.compile(checkpointer=MemorySaver())


def main() -> None:
    graph = build_graph()
    config = {"configurable": {"thread_id": "plan-protocol-probe"}}

    first = graph.invoke({"messages": []}, config)
    assert "__interrupt__" in first
    assert first["__interrupt__"][0].value["plan"] == ["强化项目架构描述"]

    revised = graph.invoke(
        Command(resume={"action": "suggest", "suggestion": "突出结果"}), config
    )
    assert "__interrupt__" in revised
    assert revised["__interrupt__"][0].value["plan"] == ["根据用户建议强化项目结果"]

    graph.invoke(Command(resume={"action": "approve"}), config)
    state = graph.get_state(config).values
    messages = state["messages"]
    assert state["edit_executed"] is True
    assert len(messages) == 4
    assert isinstance(messages[0], AIMessage)
    assert messages[0].tool_calls[0]["name"] == "request_plan"
    assert isinstance(messages[1], ToolMessage)
    assert messages[1].tool_call_id == messages[0].tool_calls[0]["id"]
    assert json.loads(str(messages[1].content))["status"] == "approved"
    assert isinstance(messages[2], AIMessage)
    assert messages[2].tool_calls[0]["name"] == "grep_replace"
    assert isinstance(messages[3], ToolMessage)
    assert not any(
        isinstance(message, AIMessage) and "批准" in str(message.content)
        for message in messages
    ), "计划批准后不应再次生成自然语言确认"
    print("PASS: request_plan 经多轮 Interrupt 后返回 approved ToolMessage，chat 直接执行编辑。")


if __name__ == "__main__":
    main()
