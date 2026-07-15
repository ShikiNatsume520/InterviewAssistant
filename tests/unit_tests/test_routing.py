"""``route_after_chat`` 并行 fan-out 路由的精简单元测试。

route 统一返回 ``list[Send]``。覆盖：
- 无消息 / 无 tool_call → ``[Send("save_memory", ...)]``。
- 单个子 agent tool_call → ``[Send(wrapper, {"tool_call": tc})]``。
- 两个子 agent tool_call（并行）→ 两条 Send。
- 全一般工具 → ``[Send("tools_node", {"messages": [last]})]``。
- 混合（子 agent + 一般工具）→ 子 agent Send + ``Send("tools_node", masked)``，
  masked 副本只含一般工具 tool_calls。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from agents.main.routing import route_after_chat


def _ai(tool_calls: list[dict]) -> AIMessage:
    """构造带 tool_calls 的 AIMessage。"""
    return AIMessage(content="", tool_calls=tool_calls)


def test_no_messages_returns_save_memory_send() -> None:
    result = route_after_chat({"messages": []})
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0].node == "save_memory"


def test_no_tool_calls_returns_save_memory_send() -> None:
    state = {"messages": [AIMessage(content="hi")], "user_id": "u1"}
    result = route_after_chat(state)
    assert len(result) == 1
    assert result[0].node == "save_memory"
    assert result[0].arg["user_id"] == "u1"


def test_single_subagent_returns_one_send() -> None:
    tc = {"name": "rag_agent", "args": {"query": "q"}, "id": "tc1"}
    result = route_after_chat({"messages": [_ai([tc])]})
    assert len(result) == 1
    assert result[0].node == "rag_agent"
    # Send 会给 tool_call 注入 "type" 字段，只校验关心的字段
    assert result[0].arg["tool_call"]["id"] == "tc1"


def test_two_subagents_parallel_returns_two_sends() -> None:
    tc1 = {"name": "rag_agent", "args": {"query": "q1"}, "id": "tc1"}
    tc2 = {"name": "resume_agent", "args": {"intent": "i"}, "id": "tc2"}
    result = route_after_chat({"messages": [_ai([tc1, tc2])]})
    assert len(result) == 2
    assert {s.node for s in result} == {"rag_agent", "resume_agent"}
    assert {s.arg["tool_call"]["id"] for s in result} == {"tc1", "tc2"}


def test_all_basic_tools_returns_tools_node_send() -> None:
    tc = {"name": "some_basic_tool", "args": {}, "id": "tc1"}
    result = route_after_chat({"messages": [_ai([tc])]})
    assert len(result) == 1
    assert result[0].node == "tools_node"
    # arg 带 messages（只含原始 last，供 ToolNode 读 tool_calls）
    assert len(result[0].arg["messages"]) == 1


def test_mixed_returns_agent_and_tools_node_sends() -> None:
    tc_agent = {"name": "rag_agent", "args": {"query": "q"}, "id": "ta"}
    tc_basic = {"name": "some_basic_tool", "args": {}, "id": "tb"}
    result = route_after_chat({"messages": [_ai([tc_agent, tc_basic])]})
    assert len(result) == 2
    assert {s.node for s in result} == {"rag_agent", "tools_node"}

    # tools_node 的 Send arg 带 masked AIMessage 副本：只含一般工具 tool_call
    tools_send = [s for s in result if s.node == "tools_node"][0]
    masked_msg = tools_send.arg["messages"][0]
    masked_tcs = masked_msg.tool_calls
    assert len(masked_tcs) == 1
    assert masked_tcs[0]["id"] == "tb"  # 只有一般工具，不含 agent 的

    # agent 的 Send arg 带它自己的 tool_call
    agent_send = [s for s in result if s.node == "rag_agent"][0]
    assert agent_send.arg["tool_call"]["id"] == "ta"
