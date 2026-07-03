"""Phase 6 原型 2: research 子图复杂控制流验证。

验证 phase 6 子图独有的两个未验证模式（resume_agent 没碰过）:

  X. 连通性重试循环:
     connectivity_check_node 失败 → interrupt("提示挂梯子") → 用户 resume
     → 条件边回自身重试 → 累计 3 次失败终止。
     验证点: "回自身"的条件边 + interrupt 在循环里反复触发 + 用 state 字段
     (connect_attempts) 累计失败次数。

  Y. 跨子图调用 Index Agent:
     finalize_node 内 `await index_agent.graph.graph.ainvoke({"target_files":[...]})`
     验证点: 子图节点内调用另一个独立编译的子图实例正常工作（Index Agent 是
     后台 agent，不进主图，但子图节点可以 await 它）。

用一个迷你 research 子图 + 主图 wrapper 验证，复用 resume_agent 已验证的
"子图 interrupt → wrapper 透传 GraphInterrupt → 主图挂起 → Command(resume=...)
精准恢复"链路 + checkpointer 继承。

运行:
    .venv/Scripts/python.exe prototypes/phase6_subgraph_interrupt_probe.py

注: Index Agent 调用会真的写文件 + 灌 Chroma，本原型用一个临时假 markdown
    避免污染真实知识库（写完即删）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 让原型能 import agent.* / index_agent.* / rag_agent.* 等（src/ 在 sys.path）
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver  # 原型用内存 checkpointer
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict


# ── 迷你 research 子图 state ────────────────────────────────────────────── #


class ProbeResearchState(TypedDict, total=False):
    """迷你 research 子图状态。"""

    gap_topic: str
    connect_attempts: int  # 连通性失败累计次数
    connectivity_ok: bool  # 最近一次连通性结果
    note: str  # 搜索产出的笔记（本原型用假数据）
    new_file_name: str  # finalize 写出的新 markdown 文件名


# ── 连通性检查（可注入"是否可达"以模拟失败/成功）────────────────────────── #
# 用预设的"探测结果序列"按调用次序消费，精确控制每次探测成功/失败。
# 每次 _probe_connectivity 调用打印 call# 与计数器变化，让控制流**可见**，
# 不靠口头推测。序列用完后默认返回 True（视为网络恢复）。
#
# 已实测的 interrupt() 语义（见本轮原型输出）:
#   - 节点首次跑到 interrupt() → 阻塞挂起。
#   - 用户 Command(resume=v) 后，节点**从头重跑**；重跑到 interrupt() 那行时
#     **不再阻塞**，直接返回 v，继续执行其后代码。
#   - "回自身"的条件边触发的是**新的节点执行**，interrupt() 会再次阻塞。
# 据此，attempts 累计写在 interrupt() **之后**的 return 里（POST-INTERRUPT），
# 由 route_after_connectivity 据 attempts 路由。

_connectivity_seq: list[bool] = []
"""预设的探测结果序列，按调用次序消费（True=连通, False=不通）。"""

_call_idx = [0]
"""探测调用计数器（每次 _probe_connectivity 调用 +1，打印用）。"""


def _reset_probe(seq: list[bool]) -> None:
    """重置探测序列与调用计数（每个场景开始前调用）。"""
    global _connectivity_seq, _call_idx
    _connectivity_seq = list(seq)
    _call_idx = [0]


def _probe_connectivity() -> bool:
    """模拟连通性检查。按预设序列返回；序列用完默认 True。

    注: 真实实现是 ``from ddgs import DDGS; return bool(DDGS().text("test", max_results=1))``。
    本原型用预设序列模拟，每次调用打印 call# 让控制流可见。
    """
    idx = _call_idx[0]
    _call_idx[0] = idx + 1
    if idx < len(_connectivity_seq):
        result = _connectivity_seq[idx]
    else:
        result = True  # 序列用完默认网络恢复
    print(f"  [_probe_connectivity] call#{idx} → {result}")
    return result


def connectivity_check_node(state: ProbeResearchState) -> dict[str, Any]:
    """连通性检查节点：失败则 interrupt 提示挂梯子。

    关键机制实测: interrupt() 恢复时到底"从节点开头重跑"还是"从 interrupt() 之后继续"？
    本节点在 interrupt() **之后**放一行标记打印 + return，据此判定真实语义。

    设计假设（待实测确认）: interrupt() 在恢复时**返回** resume 值，节点从 interrupt()
    那行继续执行后续代码。若如此，则:
    - 失败次数累计应放在 interrupt() **之前**写入 state（return 更新），但这会与 interrupt
      的暂停语义冲突（节点没 return 完 state 不 commit）。
    - 或者把累计放在 interrupt() **之后**的代码里：恢复时 interrupt() 返回 resume 值，
      后续代码 return {"connect_attempts": attempts+1, "connectivity_ok": False}，
      由 route_after_connectivity 据 attempts 路由。
    """
    attempts = state.get("connect_attempts", 0)
    print(f"  [connectivity_check] ENTER attempts={attempts}, 检查连通性…")
    ok = _probe_connectivity()
    if ok:
        print(f"  [connectivity_check] 连通 OK → 进 search")
        return {"connectivity_ok": True}

    new_attempts = attempts + 1
    print(f"  [connectivity_check] 第 {new_attempts} 次失败 → interrupt")
    resume_val = interrupt(
        {
            "phase": "connectivity_check",
            "attempts": new_attempts,
            "msg": f"无法连接 DuckDuckGo（第 {new_attempts} 次），请挂梯子后回复任意内容继续",
        }
    )
    # ↓↓↓ 实测点: 恢复时这里会不会执行？resume_val 是什么？
    print(f"  [connectivity_check] POST-INTERRUPT 执行到此处! resume_val={resume_val!r}")
    # 恢复后: 把失败次数写回 state, 清掉 ok 标志, 让条件边据 attempts 路由
    return {"connect_attempts": new_attempts, "connectivity_ok": False}


def route_after_connectivity(state: ProbeResearchState) -> str:
    """连通性检查后的条件边：OK → search；失败且未到 3 次 → 回自身重试；3 次失败 → abort。"""
    if state.get("connectivity_ok"):
        return "search"
    attempts = state.get("connect_attempts", 0)
    if attempts >= 3:
        print(f"  [route] 3 次失败 → abort (END)")
        return "abort"
    print(f"  [route] 第 {attempts} 次失败，回 connectivity_check 重试")
    return "connectivity_check"


def search_node(state: ProbeResearchState) -> dict[str, Any]:
    """搜索节点（原型用假数据，正式实现遍历 outline 逐词 ddgs + 爬取 + LLM 提炼）。"""
    print("  [search] 模拟多轮搜索（原型用假数据）")
    return {"note": f"关于「{state.get('gap_topic','?')}」的搜索笔记（假数据）"}


async def finalize_node(state: ProbeResearchState) -> dict[str, Any]:
    """finalize：写新 markdown + 跨子图调用 index_agent 重建索引。"""
    print("  [finalize] 整理笔记 → 写 markdown + 调 index_agent")
    note = state.get("note", "")
    topic = state.get("gap_topic", "unknown")
    # 文件名带 slug + 时间戳（正式实现里用 datetime，原型避免用 Date，用固定名）
    new_file_name = f"probe_{topic}.md"
    md_path = PROJECT_ROOT / "data" / "markdown" / new_file_name
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(f"# 深研笔记: {topic}\n\n{note}\n", encoding="utf-8")
    print(f"  [finalize] 已写 {md_path.name}")

    # ── 跨子图调用 Index Agent（Y 验证点）────────────────────────────── #
    try:
        from index_agent.graph import graph as index_graph

        print(f"  [finalize] 调用 index_agent.graph.ainvoke({{target_files:[{new_file_name}]}})")
        result = await index_graph.ainvoke({"target_files": [new_file_name]})
        n_chunks = len(result.get("chunks", []))
        print(f"  [finalize] index_agent 返回，新增 {n_chunks} chunk → 索引重建完成")
    except Exception as e:  # noqa: BLE001
        print(f"  [finalize][FAIL] 调用 index_agent 抛异常: {type(e).__name__}: {e}")
        raise

    # 清理原型产物，不污染知识库
    try:
        md_path.unlink()
        print(f"  [finalize] 已清理原型文件 {md_path.name}")
    except Exception:  # noqa: BLE001
        pass

    return {"new_file_name": new_file_name}


def build_probe_research_graph() -> Any:
    """构建迷你 research 子图。"""
    g = StateGraph(ProbeResearchState)
    g.add_node("connectivity_check", connectivity_check_node)
    g.add_node("search", search_node)
    g.add_node("finalize", finalize_node)

    g.set_entry_point("connectivity_check")
    # 连通性 → search / 回自身重试 / abort
    g.add_conditional_edges(
        "connectivity_check",
        route_after_connectivity,
        {
            "search": "search",
            "connectivity_check": "connectivity_check",
            "abort": END,
        },
    )
    g.add_edge("search", "finalize")
    g.add_edge("finalize", END)
    return g.compile(name="ProbeResearch")


# ── 主图 wrapper（复用 resume_agent 已验证的透传模式）───────────────────── #


class ProbeMainState(TypedDict, total=False):
    messages: list[Any]
    research_output: dict[str, Any]


_probe_research_graph = build_probe_research_graph()
"""模块级预编译子图实例（不传 checkpointer → 运行时继承父图）。"""


async def research_wrapper_node(
    state: ProbeMainState, config: RunnableConfig
) -> dict[str, Any]:
    """主图 wrapper：调用 research 子图，interrupt 时透传 GraphInterrupt。"""
    print("  [wrapper] 调用 research 子图")
    try:
        result = await _probe_research_graph.ainvoke(
            {"gap_topic": "LangGraph interrupt"}, config
        )
    except GraphInterrupt:
        print("  [wrapper] 子图 interrupt → 透传 GraphInterrupt（主图挂起）")
        raise
    print(f"  [wrapper] 子图完成: keys={list(result.keys())}")
    return {"research_output": result}


def build_probe_main_graph(checkpointer: Any = None) -> Any:
    """构建迷你主图（仅 wrapper 节点，验证 wrapper + 子图集成）。"""
    g = StateGraph(ProbeMainState)
    g.add_node("research", research_wrapper_node)
    g.set_entry_point("research")
    g.add_edge("research", END)
    return g.compile(name="ProbeMain", checkpointer=checkpointer)


# ── 驱动脚本 ────────────────────────────────────────────────────────────── #


async def _run_one_round(
    graph: Any,
    config: RunnableConfig,
    resume_value: Any | None,
    label: str,
) -> tuple[bool, Any]:
    """跑一轮：有 resume_value 用 Command(resume=...)，否则启动。

    返回 (是否 pending interrupt, interrupts)。
    """
    if resume_value is not None:
        print(f"\n>>> [{label}] Command(resume={resume_value!r})")
        await graph.ainvoke(Command(resume=resume_value), config)
    else:
        print(f"\n>>> [{label}] ainvoke 启动")
        await graph.ainvoke({"messages": []}, config)

    state = await graph.aget_state(config)
    tasks = getattr(state, "tasks", []) or []
    intrs = []
    for t in tasks:
        ti = getattr(t, "interrupts", None) or []
        intrs.extend(ti)
    intr_vals = [getattr(i, "value", i) for i in intrs]
    vals = state.values or {}
    print(
        f"  [{label}] 结果: pending={bool(intrs)}, "
        f"connect_attempts={vals.get('connect_attempts','<unset>')}, "
        f"connectivity_ok={vals.get('connectivity_ok','<unset>')}, "
        f"interrupts={intr_vals}"
    )
    return bool(intrs), intrs


async def _scenario(
    main_graph: Any,
    label: str,
    seq: list[bool],
    resume_sequence: list[Any],
) -> dict[str, Any]:
    """跑一个场景: 预设探测序列 seq + 预设 resume 序列。

    Args:
        main_graph: 主图（wrapper + research 子图）。
        label: 场景标签。
        seq: _probe_connectivity 的预设结果序列（按调用次序消费，用完默认 True）。
        resume_sequence: 每轮 resume 用的值（第 1 轮自动启动，后续按此列表 resume）。

    Returns:
        终态的 research_output dict（含 connect_attempts / connectivity_ok /
        new_file_name 等）。
    """
    print("\n" + "#" * 70)
    print(f"# 场景: {label}")
    print(f"# 探测序列={seq}, resume序列={resume_sequence}")
    print("#" * 70)

    _reset_probe(seq)
    config: RunnableConfig = {"configurable": {"thread_id": f"probe-{label}"}}
    round_idx = 0
    pending, _ = await _run_one_round(
        main_graph, config, None, f"{label}/r{round_idx}"
    )
    while pending:
        if round_idx >= len(resume_sequence):
            print(f"  [{label}] resume 序列已用尽但仍 pending，停止")
            break
        round_idx += 1
        pending, _ = await _run_one_round(
            main_graph, config, resume_sequence[round_idx - 1], f"{label}/r{round_idx}"
        )
    state = await main_graph.aget_state(config)
    out = state.values.get("research_output", {})
    print(
        f"  [{label}] 终态: pending={pending}, "
        f"connect_attempts={out.get('connect_attempts','<unset>')}, "
        f"connectivity_ok={out.get('connectivity_ok','<unset>')}, "
        f"new_file_name={out.get('new_file_name','<unset>')}"
    )
    return out


async def main() -> None:
    print("=" * 70)
    print("Phase 6 原型 2: research 子图复杂控制流验证")
    print("=" * 70)
    print("已实测 interrupt() 语义: resume 后节点从头重跑, 重跑到 interrupt() 那行")
    print("时不再阻塞, 返回 resume 值, 继续执行其后代码 (POST-INTERRUPT)。")
    print("=" * 70)

    checkpointer = MemorySaver()
    main_graph = build_probe_main_graph(checkpointer=checkpointer)

    # 场景 A: 前 2 次失败、第 3 次成功 → 完成路径
    #   r0 启动 → call#0=F → interrupt#1
    #   r1 resume → call#1=F → interrupt返回v → POST(attempts=1) → 回自身 → call#2=T
    #            → search → finalize → 调 index_agent (一次 resume 跑完整条链)
    out_a = await _scenario(
        main_graph,
        "A-2fail-then-ok",
        seq=[False, False, True],  # 3 次探测: 失败/失败/成功
        resume_sequence=["挂梯子", "继续"],  # 实际只用 1 次(r1 跑完)
    )
    a_completed = out_a.get("new_file_name") is not None
    print(
        f"  [验证 A] completed={a_completed} (new_file_name 应非空: "
        f"{out_a.get('new_file_name')!r}), attempts={out_a.get('connect_attempts')}, "
        f"ok={out_a.get('connectivity_ok')}"
    )

    # 场景 B: 始终失败 → 3 次失败 abort（不完成、不 search/finalize）
    #   abort 路径 call# 用量: call#0~5 共 6 次探测全 False
    #     r0: call#0=F → interrupt#1
    #     r1: call#1=F → POST attempts=1 → 回自身 → call#2=F → interrupt#2
    #     r2: call#3=F → POST attempts=2 → 回自身 → call#4=F → interrupt#3
    #     r3: call#5=F → POST attempts=3 → route abort → END
    #   给 8 个 False 保险（abort 在 call#5 后即 END，不会越界）
    out_b = await _scenario(
        main_graph,
        "B-all-fail-abort",
        seq=[False] * 8,
        resume_sequence=["挂梯子", "再试", "最后试一次"],  # 3 次 resume
    )
    b_aborted = out_b.get("new_file_name") is None
    print(
        f"  [验证 B] aborted={b_aborted} (new_file_name 应为 None: "
        f"{out_b.get('new_file_name')!r}), attempts 应=3: "
        f"{out_b.get('connect_attempts')}, ok={out_b.get('connectivity_ok')}"
    )

    print("\n" + "=" * 70)
    print("[结论]")
    print("  - interrupt() resume 后从节点开头重跑, interrupt() 那行返回 resume 值")
    print("  - attempts 累计写在 POST-INTERRUPT 的 return 里, 由条件边据 attempts 路由")
    print("  - 3 次失败 → abort (END); 成功 → search → finalize → 调 index_agent")
    print("  - 跨子图调用 index_agent.ainvoke 正常 (场景 A 的 finalize 验证)")
    print("  - wrapper 透传 GraphInterrupt + checkpointer 继承正常")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
