"""阶段 0 探针 1：验证取消执行与 checkpoint 恢复的真实边界。

只验证机制，不依赖项目正式图，也不修改 ``src/``：

1. 节点执行中取消 ``astream``，最近 checkpoint 是否仍停在该节点之前；
2. ``astream(None, config)`` 是否会从最近 checkpoint 重新执行该节点；
3. 节点在返回前已经产生外部副作用时，取消后恢复是否会重复副作用。

结论用于约束“切换会话自动中断、下次自动恢复”的产品语义。
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict


class ProbeState(TypedDict, total=False):
    """探针状态。"""

    phase: str
    trace: Annotated[list[str], list.__add__]


entered = asyncio.Event()
release = asyncio.Event()
attempts = 0
external_effects: list[str] = []


async def prepare_node(_state: ProbeState) -> dict[str, Any]:
    """产生一个确定已提交的 checkpoint。"""
    return {"phase": "prepared", "trace": ["prepare"]}


async def risky_node(_state: ProbeState) -> dict[str, Any]:
    """模拟长任务：先产生外部副作用，再等待，最后才返回 state update。"""
    global attempts
    attempts += 1
    external_effects.append(f"effect-attempt-{attempts}")
    entered.set()
    await release.wait()
    return {"phase": "completed", "trace": [f"risky-{attempts}"]}


def build_graph() -> Any:
    """构建带内存 checkpointer 的最小图。"""
    workflow = StateGraph(ProbeState)
    workflow.add_node("prepare", prepare_node)
    workflow.add_node("risky", risky_node)
    workflow.add_edge(START, "prepare")
    workflow.add_edge("prepare", "risky")
    workflow.add_edge("risky", END)
    return workflow.compile(checkpointer=MemorySaver())


async def drain(stream: Any) -> None:
    """消费图流，模拟 SSE 响应生成器持续迭代。"""
    async for _chunk in stream:
        pass


async def main() -> None:
    """执行取消与恢复探针。"""
    graph = build_graph()
    config: dict[str, Any] = {"configurable": {"thread_id": "phase0-cancel"}}

    print("=== A. 启动图，等待 risky 节点已产生外部副作用 ===")
    task = asyncio.create_task(
        drain(graph.astream({"phase": "new", "trace": []}, config))
    )
    await asyncio.wait_for(entered.wait(), timeout=5)
    print(f"attempts={attempts}, external_effects={external_effects}")

    print("\n=== B. 模拟客户端断开：取消正在消费 astream 的任务 ===")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        print("astream consumer 收到 CancelledError")

    state_after_cancel = await graph.aget_state(config)
    print(f"state.values={state_after_cancel.values}")
    print(f"state.next={state_after_cancel.next}")

    assert state_after_cancel.values.get("phase") == "prepared"
    assert state_after_cancel.next == ("risky",)
    assert external_effects == ["effect-attempt-1"]

    print("\n=== C. 释放节点并从最近 checkpoint 续跑 ===")
    release.set()
    await drain(graph.astream(None, config))
    final_state = await graph.aget_state(config)
    print(f"final.values={final_state.values}")
    print(f"final.next={final_state.next}")
    print(f"attempts={attempts}, external_effects={external_effects}")

    assert final_state.values.get("phase") == "completed"
    assert final_state.next == ()
    assert attempts == 2
    assert external_effects == ["effect-attempt-1", "effect-attempt-2"]

    print("\n=== 机制结论 ===")
    print("PASS: 节点中途取消后，checkpoint 保持在节点执行前。")
    print("PASS: astream(None) 会从 checkpoint 重新执行未完成节点。")
    print("RISK: 节点返回前发生的外部副作用会在恢复时重复。")


if __name__ == "__main__":
    asyncio.run(main())
