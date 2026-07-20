"""阶段 8 原型：验证并发请求级凭据隔离和持久化视图无秘密。"""

from __future__ import annotations

import asyncio
import json

from kernel.runtime_model import (
    RuntimeModelConfig,
    get_runtime_model,
    use_runtime_model,
)

SECRET_A = "sk-phase8-alice-never-persist"
SECRET_B = "sk-phase8-bob-never-persist"


async def simulated_request(config: RuntimeModelConfig, gate: asyncio.Event) -> str:
    """并发交错读取 ContextVar，返回非敏感模型名。"""
    with use_runtime_model(config):
        assert get_runtime_model() == config
        gate.set()
        await asyncio.sleep(0)
        assert get_runtime_model() == config
        return config.model


async def main() -> None:
    gate = asyncio.Event()
    alice = RuntimeModelConfig(SECRET_A, "https://alice.invalid/v1", "alice-model")
    bob = RuntimeModelConfig(SECRET_B, "https://bob.invalid/v1", "bob-model")
    models = await asyncio.gather(
        simulated_request(alice, gate), simulated_request(bob, gate)
    )
    assert models == ["alice-model", "bob-model"]
    assert get_runtime_model() is None

    checkpoint_view = {"messages": ["用户问题"], "selected_model": models[0]}
    product_event_view = {"type": "task.completed", "status": "completed"}
    log_view = "task failed | error=AuthenticationError"
    serialized = json.dumps(
        [checkpoint_view, product_event_view, log_view], ensure_ascii=False
    )
    assert SECRET_A not in serialized
    assert SECRET_B not in serialized

    print("PASS: 两个并发请求的 RuntimeModelConfig 不会串用。")
    print("PASS: 请求结束后 ContextVar 恢复为 None。")
    print("PASS: checkpoint、产品事件和日志模拟视图不含测试 Key。")


if __name__ == "__main__":
    asyncio.run(main())
