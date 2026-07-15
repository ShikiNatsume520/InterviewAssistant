"""主图条件路由。

R1 重构：``route_after_chat`` 从 if-elif 链改为查 ``ROUTE_TABLE``。
route 函数不是节点，Studio 不分析它，查表完全没问题。

并行调用支持：``route_after_chat`` 统一返回 ``list[Send]``——对每个子 agent
tool_call 发一个 Send 到其 wrapper（arg 带该 tool_call），对一般工具调用发一个
Send 到 ``tools_node``（arg 带"只含一般工具的 AIMessage 副本"，避免 ToolNode 撞上
子 agent 工具的 ``raise RuntimeError``），无 tool_call 时发一个 Send 到
``save_memory``（arg 带完整 messages + user_id 供其提取事实）。

主图 state 的原始 AIMessage 始终不被修改——掩盖只发生在发给 tools_node 的
Send arg 副本里，故 wrapper/ToolNode 返回的 ToolMessage 都能按原始 tool_call_id
匹配回主图 AIMessage，无需"还原"。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
from langgraph.types import Send

from agents.main.registry import ROUTE_TABLE
from agents.main.state import MainState
from kernel.logging import dlog


def route_after_chat(state: MainState) -> list[Send]:
    """``chat_node`` 的条件路由，统一返回 ``list[Send]`` 支持并行多 tool_call。

    - 无 ``tool_call`` → ``[Send("save_memory", {messages, user_id})]``
    - 全子 agent → 对每个 tool_call 发 ``Send(wrapper, {"tool_call": tc})``
    - 全一般工具 → ``[Send("tools_node", {"messages": [last]})]``
    - 混合 → 子 agent 的 Send + ``Send("tools_node", {"messages": [masked]})``，
      masked 是只含一般工具 tool_calls 的 AIMessage 副本（主图 AIMessage 不动）
    """
    messages = state.get("messages", [])
    user_id = state.get("user_id", "default")

    if not messages:
        dlog("main", "route_after_chat", "无消息 → save_memory")
        return [Send("save_memory", {"user_id": user_id})]

    last = messages[-1]
    tool_calls = getattr(last, "tool_calls", []) or []
    if not tool_calls:
        dlog("main", "route_after_chat", "无 tool_call → save_memory")
        return [Send("save_memory", {"messages": messages, "user_id": user_id})]

    agent_sends: list[Send] = []
    basic_tcs: list[dict[str, Any]] = []
    for tc in tool_calls:
        name = str(tc.get("name", ""))
        node = ROUTE_TABLE.get(name)
        if node:
            agent_sends.append(Send(node, {"tool_call": tc}))
        else:
            basic_tcs.append(tc)

    if not agent_sends and not basic_tcs:
        dlog("main", "route_after_chat", "无有效 tool_call → save_memory")
        return [Send("save_memory", {"messages": messages, "user_id": user_id})]

    if not agent_sends:
        dlog("main", "route_after_chat", f"→ tools_node（{len(basic_tcs)} 个一般工具）")
        return [Send("tools_node", {"messages": [last]})]

    if not basic_tcs:
        dlog(
            "main",
            "route_after_chat",
            f"fan-out → {[s.node for s in agent_sends]}",
        )
        return agent_sends

    # 混合：并行 Send 子 agent + Send(tools_node, masked AIMessage 副本)
    masked = AIMessage(content=last.content, tool_calls=basic_tcs)
    agent_sends.append(Send("tools_node", {"messages": [masked]}))
    dlog(
        "main",
        "route_after_chat",
        f"混合 fan-out → agents={[s.node for s in agent_sends[:-1]]}, "
        f"basic_tools={len(basic_tcs)}",
    )
    return agent_sends
