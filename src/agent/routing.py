"""主图条件路由。

R1 重构：``route_after_chat`` 从 if-elif 链改为查 ``ROUTE_TABLE``。
route 函数不是节点，Studio 不分析它，查表完全没问题。
"""

from __future__ import annotations

from agent.registry import ROUTE_TABLE
from agent.state import MainState
from kernel.logging import dlog


def route_after_chat(state: MainState) -> str:
    """``chat_node`` 的条件路由。

    查 ``ROUTE_TABLE`` 分发到对应子智能体 wrapper，未命中则 ``tools_node``，
    无 tool_call 则 ``save_memory``。

    - 无 ``tool_call`` → ``"save_memory"``
    - 工具名在 ``ROUTE_TABLE`` → 对应 ``route_key``
    - 否则 → ``"tools_node"``
    """
    messages = state.get("messages", [])
    if not messages:
        dlog("main", "route_after_chat", "无消息 → save_memory")
        return "save_memory"

    tool_calls = getattr(messages[-1], "tool_calls", [])
    if not tool_calls:
        dlog("main", "route_after_chat", "无 tool_call → save_memory")
        return "save_memory"

    tool_name: str = str(tool_calls[0].get("name", ""))
    target: str = ROUTE_TABLE.get(tool_name, "tools_node")
    dlog("main", "route_after_chat", f"→ {target} (tool={tool_name})")
    return target
