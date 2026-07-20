"""阶段 0 探针 3：验证运行时凭据贯穿父/子图且不进入 checkpoint。

使用假的 API Key 标记，不发起网络请求。探针检查：

1. 父图节点能从 ``RunnableConfig`` 读取运行时模型配置；
2. 静态 wrapper 调用的子图继承同一运行时配置；
3. 节点不把凭据写入 state 时，MemorySaver checkpoint 中不出现凭据标记。
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

SECRET_MARKER = "sk-phase0-never-persist-1234567890"


class ProbeState(TypedDict, total=False):
    """只保存非敏感验证结果。"""

    messages: Annotated[list[BaseMessage], add_messages]
    parent_saw_secret: bool
    child_saw_secret: bool
    selected_model: str


def runtime_model(config: RunnableConfig) -> dict[str, str]:
    """从 config 读取模拟运行时模型配置。"""
    value = config.get("configurable", {}).get("runtime_model", {})
    return value if isinstance(value, dict) else {}


async def child_node(_state: ProbeState, config: RunnableConfig) -> dict[str, Any]:
    """验证子图继承运行时配置，但只返回布尔结果。"""
    model = runtime_model(config)
    return {"child_saw_secret": model.get("api_key") == SECRET_MARKER}


child_workflow = StateGraph(ProbeState)
child_workflow.add_node("child", child_node)
child_workflow.add_edge(START, "child")
child_workflow.add_edge("child", END)
child_graph = child_workflow.compile(name="RuntimeSecretChild")


async def parent_node(_state: ProbeState, config: RunnableConfig) -> dict[str, Any]:
    """验证父图读取配置，并调用继承 config 的子图。"""
    model = runtime_model(config)
    child_result = await child_graph.ainvoke({}, config)
    return {
        "parent_saw_secret": model.get("api_key") == SECRET_MARKER,
        "child_saw_secret": bool(child_result.get("child_saw_secret", False)),
        "selected_model": str(model.get("model", "")),
    }


def build_graph(checkpointer: MemorySaver) -> Any:
    """构建父图。"""
    workflow = StateGraph(ProbeState)
    workflow.add_node("parent", parent_node)
    workflow.add_edge(START, "parent")
    workflow.add_edge("parent", END)
    return workflow.compile(name="RuntimeSecretParent", checkpointer=checkpointer)


async def main() -> None:
    """执行运行时凭据与 checkpoint 泄漏检查。"""
    saver = MemorySaver()
    graph = build_graph(saver)
    config: dict[str, Any] = {
        "configurable": {
            "thread_id": "phase0-runtime-secret",
            "runtime_model": {
                "provider": "openai-compatible",
                "base_url": "https://example.invalid/v1",
                "model": "phase0-model",
                "api_key": SECRET_MARKER,
            },
        }
    }

    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="probe")]}, config
    )
    print(f"result={result}")
    assert result["parent_saw_secret"] is True
    assert result["child_saw_secret"] is True
    assert result["selected_model"] == "phase0-model"

    snapshot = await graph.aget_state(config)
    checkpoint_rows = list(saver.list(config))
    serialized_view = repr((snapshot, checkpoint_rows))

    print(f"checkpoint_count={len(checkpoint_rows)}")
    print(f"snapshot.values={snapshot.values}")
    print(f"secret_in_checkpoint_view={SECRET_MARKER in serialized_view}")

    assert SECRET_MARKER not in repr(snapshot.values)
    assert SECRET_MARKER not in serialized_view

    print("\n=== 机制结论 ===")
    print("PASS: 父图和静态 wrapper 子图都能读取运行时模型配置。")
    print("PASS: 未写入 state 的 API Key 标记没有出现在 MemorySaver checkpoint。")


if __name__ == "__main__":
    asyncio.run(main())
