"""Phase 7 原型: 主图并行多子 agent 调用 (Send API fan-out) 可行性验证。

背景
----
当前主图 ``route_after_chat`` 只看 ``tool_calls[0]`` 返回单个 route_key，
LLM 一次响应里并行调两个不同子 agent 时，第二个 tool_call 会被丢弃，且
因 assistant tool_calls 没有对应 ToolMessage 会导致 OpenAI 400。

方案 A: ``route_after_chat`` 返回 ``list[Send]``，对每个子 agent 的
tool_call 发一个 ``Send(node, arg)`` 到对应 wrapper，wrapper 从 messages
挑自己的 tool_call 并返回 ToolMessage，由 ``add_messages`` reducer 合并。

本原型验证三个关键风险点:
  1. 普通并行: 两个子图都直接完成 → 两个 wrapper 都跑、两条 ToolMessage
     都进 messages、chat_node 重入能看到两条。
  2. interrupt 交互: 一个子图 interrupt 挂起、另一个完成 → 主图整体挂起，
     完成那侧的 ToolMessage 是否保留在 state（不被 interrupt 侧丢掉）。
  3. resume 恢复: Command(resume=...) 后 interrupt 侧继续跑完，与已完成的
     那侧 ToolMessage 共存，chat_node 重入时两条都在。

迷你子图:
  - quick_sub: 直接返回 note，不 interrupt。
  - slow_sub: 先 interrupt 一次（模拟"请确认"），resume 后返回 note。

运行:
    uv run python prototypes/phase7_parallel_toolcalls_probe.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Annotated, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, Send, interrupt
from typing_extensions import TypedDict


# ── 迷你子图 state ──────────────────────────────────────────────────────── #


class QuickState(TypedDict, total=False):
    """快子图 state（不 interrupt）。"""

    query: str
    note: str


class SlowState(TypedDict, total=False):
    """慢子图 state（含 interrupt）。"""

    query: str
    note: str


def quick_node(state: QuickState) -> dict[str, Any]:
    """快子图：直接返回 note。"""
    q = state.get("query", "?")
    print(f"    [quick_sub] 跑完 query={q!r}")
    return {"note": f"quick 结果 for {q}"}


def slow_node(state: SlowState) -> dict[str, Any]:
    """慢子图：interrupt 一次后返回 note。"""
    q = state.get("query", "?")
    print(f"    [slow_sub] ENTER query={q!r}, 准备 interrupt")
    resume_val = interrupt({"phase": "slow_confirm", "msg": f"确认要做 {q} 吗？"})
    print(f"    [slow_sub] POST-INTERRUPT resume_val={resume_val!r}, 继续跑完")
    return {"note": f"slow 结果 for {q} (confirmed={resume_val!r})"}


def build_quick_graph() -> Any:
    g = StateGraph(QuickState)
    g.add_node("quick_node", quick_node)
    g.set_entry_point("quick_node")
    g.add_edge("quick_node", END)
    return g.compile(name="QuickSub")


def build_slow_graph() -> Any:
    g = StateGraph(SlowState)
    g.add_node("slow_node", slow_node)
    g.set_entry_point("slow_node")
    g.add_edge("slow_node", END)
    return g.compile(name="SlowSub")


_quick_graph = build_quick_graph()
_slow_graph = build_slow_graph()
"""模块级预编译子图实例（不传 checkpointer → 运行时继承父图）。"""


# ── 主图 state ──────────────────────────────────────────────────────────── #


class ProbeMainState(TypedDict, total=False):
    """主图 state：messages 用 add_messages reducer 合并多 wrapper 输出。"""

    messages: Annotated[list[Any], add_messages]


# ── 模拟 chat_node：产出带多个 tool_calls 的 AIMessage ─────────────────── #


def make_chat_output(tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    """构造 chat_node 输出：一条带多个 tool_calls 的 AIMessage。"""
    return {"messages": [AIMessage(content="我来并行调用", tool_calls=tool_calls)]}


# ── wrapper 节点：从 Send 传入的 tool_call 调用子图 ─────────────────────── #


async def quick_wrapper(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """quick_agent wrapper：从 Send 传入的 tool_call 调用 quick 子图，返回 ToolMessage。

    注意：Send(node, arg) 的 arg 会**替换**核心 graph state 传给该节点，故 wrapper
    无法从 state["messages"] 读主图消息——tool_call 必须由 Send 的 arg 显式传入。
    返回的 ToolMessage 由主图 ``add_messages`` reducer 合并到主图 messages。
    """
    tc = state.get("tool_call", {}) or {}
    tc_id = str(tc.get("id", "quick_agent"))
    query = str(tc.get("args", {}).get("query", ""))
    print(f"  [quick_wrapper] query={query!r} tc_id={tc_id}")
    result = await _quick_graph.ainvoke({"query": query}, config)
    note = result.get("note", "")
    return {"messages": [ToolMessage(content=f"quick: {note}", tool_call_id=tc_id)]}


async def slow_wrapper(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """slow_agent wrapper：从 Send 传入的 tool_call 调用 slow 子图，interrupt 时透传。"""
    tc = state.get("tool_call", {}) or {}
    tc_id = str(tc.get("id", "slow_agent"))
    query = str(tc.get("args", {}).get("query", ""))
    print(f"  [slow_wrapper] query={query!r} tc_id={tc_id}")
    try:
        result = await _slow_graph.ainvoke({"query": query}, config)
    except GraphInterrupt:
        print(f"  [slow_wrapper] 子图 interrupt → 透传 GraphInterrupt（主图挂起）")
        raise
    note = result.get("note", "")
    return {"messages": [ToolMessage(content=f"slow: {note}", tool_call_id=tc_id)]}


# ── 路由：返回 list[Send] 做 fan-out ───────────────────────────────────── #


# wrapper 节点名 ↔ tool 名（一对一，模拟 REGISTRY/ROUTE_TABLE）
TOOL_TO_NODE: dict[str, str] = {
    "quick_agent": "quick_wrapper",
    "slow_agent": "slow_wrapper",
}


def route_after_chat(state: ProbeMainState) -> list[Send]:
    """返回 list[Send]：对每个子 agent tool_call 发一个 Send 到对应 wrapper。

    非子 agent tool_call 暂不在本原型处理（BASIC_TOOLS 为空）。
    无 tool_call 时返回 [] —— 但 LangGraph 要求至少返回某条边或 []，这里
    用 ``[]`` 表示无后续节点（会结束当前 superstep）。注意：真实主图无
    tool_call 要去 ``save_memory``，本原型简化为 []。
    """
    messages = state.get("messages", [])
    if not messages:
        return []
    tool_calls = getattr(messages[-1], "tool_calls", []) or []
    sends: list[Send] = []
    for tc in tool_calls:
        name = str(tc.get("name", ""))
        node = TOOL_TO_NODE.get(name)
        if node:
            # Send 的 arg 是传给节点的 state；wrapper 无法读主图 messages，
            # 故把 tool_call 本身传过去（key 固定为 "tool_call"）。
            sends.append(Send(node, {"tool_call": tc}))
    return sends


def build_probe_main_graph(checkpointer: Any = None) -> Any:
    """构建主图：chat_node → (fan-out) wrappers → chat_node。"""
    g = StateGraph(ProbeMainState)
    g.add_node("chat_node", chat_node)
    g.add_node("quick_wrapper", quick_wrapper)
    g.add_node("slow_wrapper", slow_wrapper)
    g.set_entry_point("chat_node")
    # conditional_edges 返回 list[Send] 时，path_map 可省略或为 None
    g.add_conditional_edges("chat_node", route_after_chat)
    # 各 wrapper 完成后回 chat_node
    g.add_edge("quick_wrapper", "chat_node")
    g.add_edge("slow_wrapper", "chat_node")
    return g.compile(name="ProbeMainParallel", checkpointer=checkpointer)


# ── chat_node：用"脚本"驱动多轮，模拟 LLM 决策 ────────────────────────── #


# 预设的 chat_node 行为序列：每轮产出什么 tool_calls。
# 第 0 轮：并行调 quick + slow。第 1 轮（resume 后 chat 重入）：无 tool_call，结束。
_CHAT_SCRIPT: list[dict[str, Any]] = []


def chat_node(state: ProbeMainState) -> dict[str, Any]:
    """模拟 chat_node：按脚本产出 AIMessage。

    第 0 轮（messages 里只有初始 HumanMessage 或为空）→ 产出预设的并行 tool_calls。
    之后重入（已有 ToolMessage）→ 产出无 tool_call 的纯文本回复，结束。
    """
    msgs = state.get("messages", [])
    # 判断是否首轮：messages 里还没有 AIMessage with tool_calls
    has_toolcall_ai = any(
        getattr(m, "tool_calls", None) for m in msgs if isinstance(m, AIMessage)
    )
    if not has_toolcall_ai and _CHAT_SCRIPT:
        out = make_chat_output(_CHAT_SCRIPT[0]["tool_calls"])
        tcs = _CHAT_SCRIPT[0]["tool_calls"]
        print(f"  [chat_node] 产出并行 tool_calls: {[t['name'] for t in tcs]}")
        return out
    # 已有工具结果 → 产出纯文本结束
    print("  [chat_node] 重入，已有工具结果，产出纯文本结束")
    return {"messages": [AIMessage(content="汇总两个子 agent 的结果给用户")]}


# ── 驱动脚本 ────────────────────────────────────────────────────────────── #


def _tc(name: str, tc_id: str, query: str) -> dict[str, Any]:
    """构造一个 tool_call dict。"""
    return {"name": name, "args": {"query": query}, "id": tc_id}


async def _dump_state(graph: Any, config: RunnableConfig, label: str) -> None:
    """打印当前 state 的 messages 摘要与 pending 状态。"""
    state = await graph.aget_state(config)
    vals = state.values or {}
    msgs = vals.get("messages", [])
    pending_tasks = []
    for t in getattr(state, "tasks", []) or []:
        for i in getattr(t, "interrupts", None) or []:
            pending_tasks.append(getattr(i, "value", i))
    print(f"  [{label}] pending_interrupt={bool(pending_tasks)}, msgs_n={len(msgs)}")
    for m in msgs:
        role = type(m).__name__
        if isinstance(m, AIMessage):
            tcs = [t.get("name") for t in (getattr(m, "tool_calls", []) or [])]
            print(f"    - {role} tool_calls={tcs}")
        elif isinstance(m, ToolMessage):
            print(f"    - {role} id={m.tool_call_id} content={m.content[:60]!r}")
        else:
            print(f"    - {role} content={str(m.content)[:60]!r}")
    if pending_tasks:
        print(f"    pending interrupt values: {pending_tasks}")


async def scenario_parallel_both_complete() -> None:
    """场景 1：quick + slow 都不 interrupt（slow 改为不 interrupt 不便，故用两个 quick）。

    本场景用 quick + quick（两个都直接完成）验证普通并行。
    """
    print("\n" + "#" * 70)
    print("# 场景 1: 两个子 agent 都直接完成（普通并行）")
    print("#" * 70)
    # 临时把 slow 的 tool 名也映射到 quick_wrapper，模拟"两个都直接完成"
    global TOOL_TO_NODE
    orig = dict(TOOL_TO_NODE)
    TOOL_TO_NODE = {"quick_agent": "quick_wrapper", "quick_agent_2": "quick_wrapper"}
    # chat 脚本：第 0 轮并行调两个 quick
    global _CHAT_SCRIPT
    _CHAT_SCRIPT = [
        {"tool_calls": [_tc("quick_agent", "call_q1", "Q1"), _tc("quick_agent_2", "call_q2", "Q2")]}
    ]
    cfg: RunnableConfig = {"configurable": {"thread_id": "sc1"}}
    g = build_probe_main_graph(checkpointer=MemorySaver())
    print(">>> 启动 ainvoke")
    await g.ainvoke({"messages": []}, cfg)
    await _dump_state(g, cfg, "sc1-final")
    # 验证：messages 里应有 2 条 ToolMessage + 最终 1 条纯文本 AIMessage
    state = await g.aget_state(cfg)
    msgs = state.values.get("messages", [])
    tool_msgs = [m for m in msgs if isinstance(m, ToolMessage)]
    final_ai = [m for m in msgs if isinstance(m, AIMessage) and not (getattr(m, "tool_calls", None))]
    print(f"  [验证 1] ToolMessage 数量={len(tool_msgs)} (期望 2), 纯文本 AIMessage={len(final_ai)} (期望 1)")
    assert len(tool_msgs) == 2, f"期望 2 条 ToolMessage，实际 {len(tool_msgs)}"
    assert len(final_ai) == 1, f"期望 1 条纯文本 AIMessage，实际 {len(final_ai)}"
    print("  [验证 1] PASSED ✓")
    TOOL_TO_NODE = orig


async def scenario_one_interrupt_one_complete() -> None:
    """场景 2：quick 完成 + slow interrupt 挂起。验证主图整体挂起 + quick 结果保留。"""
    print("\n" + "#" * 70)
    print("# 场景 2: quick 完成 + slow interrupt 挂起")
    print("#" * 70)
    global _CHAT_SCRIPT
    _CHAT_SCRIPT = [
        {"tool_calls": [_tc("quick_agent", "call_q", "QQ"), _tc("slow_agent", "call_s", "SS")]}
    ]
    cfg: RunnableConfig = {"configurable": {"thread_id": "sc2"}}
    g = build_probe_main_graph(checkpointer=MemorySaver())
    print(">>> 启动 ainvoke（quick 完成、slow interrupt）")
    await g.ainvoke({"messages": []}, cfg)
    await _dump_state(g, cfg, "sc2-interrupted")
    # 验证：pending=True，且 quick 的 ToolMessage 已在 state（不被 interrupt 丢掉）
    state = await g.aget_state(cfg)
    msgs = state.values.get("messages", [])
    pending = any(
        getattr(i, "value", i) is not None
        for t in (getattr(state, "tasks", []) or [])
        for i in (getattr(t, "interrupts", None) or [])
    )
    quick_tm = [m for m in msgs if isinstance(m, ToolMessage) and m.tool_call_id == "call_q"]
    print(f"  [验证 2] pending={pending} (期望 True), quick ToolMessage={len(quick_tm)} (期望 1)")
    assert pending, "期望主图挂起 pending=True"
    assert len(quick_tm) == 1, f"期望 quick 的 ToolMessage 已保留，实际 {len(quick_tm)} 条"
    print("  [验证 2] PASSED ✓ (quick 结果保留，主图整体挂起)")
    # 保存图与 config 供场景 3 复用
    return g, cfg


async def scenario_resume_after_interrupt(g: Any, cfg: RunnableConfig) -> None:
    """场景 3：resume 后 slow 跑完，与 quick 结果共存，chat 重入两条都在。"""
    print("\n" + "#" * 70)
    print("# 场景 3: Command(resume) 恢复 slow，验证两条 ToolMessage 共存")
    print("#" * 70)
    print(">>> Command(resume='yes')")
    await g.ainvoke(Command(resume="yes"), cfg)
    await _dump_state(g, cfg, "sc3-resumed")
    state = await g.aget_state(cfg)
    msgs = state.values.get("messages", [])
    pending = any(
        True
        for t in (getattr(state, "tasks", []) or [])
        for i in (getattr(t, "interrupts", None) or [])
    )
    quick_tm = [m for m in msgs if isinstance(m, ToolMessage) and m.tool_call_id == "call_q"]
    slow_tm = [m for m in msgs if isinstance(m, ToolMessage) and m.tool_call_id == "call_s"]
    final_ai = [m for m in msgs if isinstance(m, AIMessage) and not (getattr(m, "tool_calls", None))]
    print(
        f"  [验证 3] pending={pending} (期望 False), "
        f"quick ToolMessage={len(quick_tm)} (期望 1), "
        f"slow ToolMessage={len(slow_tm)} (期望 1), "
        f"最终纯文本={len(final_ai)} (期望 1)"
    )
    assert not pending, "期望 resume 后不再 pending"
    assert len(quick_tm) == 1, f"resume 后 quick 结果应仍在，实际 {len(quick_tm)} 条"
    assert len(slow_tm) == 1, f"resume 后 slow 结果应已加入，实际 {len(slow_tm)} 条"
    assert len(final_ai) == 1, f"chat 应重入产出纯文本，实际 {len(final_ai)} 条"
    print("  [验证 3] PASSED ✓ (resume 后两条 ToolMessage 共存 + chat 重入结束)")


async def main() -> None:
    print("=" * 70)
    print("Phase 7 原型: 主图并行多子 agent 调用 (Send API fan-out) 可行性验证")
    print("langgraph Send API + interrupt 并行交互")
    print("=" * 70)

    # 场景 1：两个都直接完成
    await scenario_parallel_both_complete()

    # 场景 2：一个 interrupt 一个完成
    g, cfg = await scenario_one_interrupt_one_complete()

    # 场景 3：resume 恢复
    await scenario_resume_after_interrupt(g, cfg)

    print("\n" + "=" * 70)
    print("[结论]")
    print("  - route_after_chat 返回 list[Send] 可对每个 tool_call fan-out 到对应 wrapper")
    print("  - 两个子图都完成 → 两条 ToolMessage 都进 messages（add_messages 合并）")
    print("  - 一个 interrupt + 一个完成 → 主图整体挂起，完成侧 ToolMessage 保留")
    print("  - Command(resume) 恢复 interrupt 侧，与已完成侧 ToolMessage 共存，chat 重入结束")
    print("  - 方案 A 可行，可进入正式实现")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
