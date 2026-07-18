"""Resume Agent计划批准后 ToolMessage 协议回归测试。"""

from __future__ import annotations

import json
from importlib import import_module
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

resume_graph = import_module("agents.resume.graph")


def test_approved_plan_returns_tool_result_before_editing(monkeypatch: Any) -> None:
    chat_calls = 0

    def fake_model(_model_name: str, tools: list[Any] | None = None) -> Any:
        nonlocal chat_calls
        if tools is None:
            return RunnableLambda(
                lambda _prompt: AIMessage(
                    content=json.dumps(["强化项目描述"], ensure_ascii=False)
                )
            )

        def chat_response(_prompt: Any) -> AIMessage:
            nonlocal chat_calls
            chat_calls += 1
            if chat_calls == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "request_plan", "args": {}, "id": "plan-call"}
                    ],
                )
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "grep_replace",
                        "args": {
                            "grep_target": "旧项目描述",
                            "replace_content": "强化后的项目描述",
                            "section": "项目经历",
                            "reason": "突出项目价值",
                        },
                        "id": "edit-call",
                    }
                ],
            )

        return RunnableLambda(chat_response)

    monkeypatch.setattr(resume_graph, "get_chat_model", fake_model)
    graph = resume_graph.build_resume_workflow().compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "resume-plan-test"}}
    initial = {
        "messages": [HumanMessage(content="请系统优化项目经历")],
        "resume_id": "resume-1",
        "source_display_name": "测试简历",
        "user_request": "请系统优化项目经历",
        "resume_session_id": "session-1",
        "resume_shot": "# 项目经历\n旧项目描述",
    }

    first = graph.invoke(initial, config)
    assert first["__interrupt__"][0].value["phase"] == "plan_confirm"

    revised = graph.invoke(
        Command(resume={"action": "suggest", "suggestion": "突出技术结果"}),
        config,
    )
    assert revised["__interrupt__"][0].value["phase"] == "plan_confirm"

    approved = graph.invoke(Command(resume={"action": "approve"}), config)
    assert approved["__interrupt__"][0].value["phase"] == "resume_approve"
    messages = graph.get_state(config).values["messages"]

    assert len(messages) == 4
    assert isinstance(messages[0], HumanMessage)
    assert isinstance(messages[1], AIMessage)
    assert messages[1].tool_calls[0]["name"] == "request_plan"
    assert isinstance(messages[2], ToolMessage)
    assert messages[2].tool_call_id == "plan-call"
    result = json.loads(str(messages[2].content))
    assert result["status"] == "approved"
    assert result["instruction"].startswith("计划已由用户批准")
    assert isinstance(messages[3], AIMessage)
    assert messages[3].tool_calls[0]["name"] == "grep_replace"
    assert chat_calls == 2
