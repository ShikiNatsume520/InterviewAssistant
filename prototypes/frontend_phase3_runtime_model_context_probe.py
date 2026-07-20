"""阶段 3：并发请求级模型配置穿过 LangGraph 且不进入 checkpoint 探针。"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator, TypedDict, cast

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph


@dataclass(frozen=True)
class RuntimeModelConfig:
    api_key: str
    base_url: str
    model: str


_runtime_model: ContextVar[RuntimeModelConfig | None] = ContextVar(
    "runtime_model", default=None
)


@contextmanager
def use_runtime_model(config: RuntimeModelConfig) -> Iterator[None]:
    token = _runtime_model.set(config)
    try:
        yield
    finally:
        _runtime_model.reset(token)


def get_runtime_model() -> RuntimeModelConfig:
    config = _runtime_model.get()
    if config is None:
        raise RuntimeError("runtime model config missing")
    return config


class ProbeState(TypedDict):
    observed_model: str
    observed_key_suffix: str


def sync_llm_node(state: ProbeState) -> ProbeState:
    """模拟当前项目中的同步 LLM 节点。"""
    del state
    config = get_runtime_model()
    return {
        "observed_model": config.model,
        "observed_key_suffix": config.api_key[-4:],
    }


async def invoke_as(
    graph: Any, thread_id: str, runtime: RuntimeModelConfig
) -> dict[str, str]:
    with use_runtime_model(runtime):
        result = await graph.ainvoke(
            {"observed_model": "", "observed_key_suffix": ""},
            {"configurable": {"thread_id": thread_id}},
        )
    return cast(dict[str, str], result)


async def run_probe() -> None:
    saver = MemorySaver()
    builder = StateGraph(ProbeState)
    builder.add_node("sync_llm", sync_llm_node)
    builder.add_edge(START, "sync_llm")
    builder.add_edge("sync_llm", END)
    graph = builder.compile(checkpointer=saver)

    first_key = "guest-a-secret-1111"
    second_key = "guest-b-secret-2222"
    first, second = await asyncio.gather(
        invoke_as(
            graph,
            "thread-a",
            RuntimeModelConfig(first_key, "https://a.example/v1", "model-a"),
        ),
        invoke_as(
            graph,
            "thread-b",
            RuntimeModelConfig(second_key, "https://b.example/v1", "model-b"),
        ),
    )

    assert first == {"observed_model": "model-a", "observed_key_suffix": "1111"}
    assert second == {"observed_model": "model-b", "observed_key_suffix": "2222"}
    assert _runtime_model.get() is None

    checkpoint_text = repr(saver.storage)
    assert first_key not in checkpoint_text
    assert second_key not in checkpoint_text
    assert "https://a.example/v1" not in checkpoint_text
    assert "https://b.example/v1" not in checkpoint_text

    print("PASS: ContextVar reached synchronous LangGraph nodes")
    print("PASS: concurrent guest model configs stayed isolated")
    print("PASS: context was cleared after each request")
    print("PASS: API keys and base URLs did not enter checkpoints")


if __name__ == "__main__":
    asyncio.run(run_probe())
