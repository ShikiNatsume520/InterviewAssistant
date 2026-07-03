#!/usr/bin/env python3
"""Phase 5 SDK 端到端测试 — 通过 FastAPI /v1/chat SSE 验证完整链路。

测试内容
--------
1. **激活 resume_agent → plan_confirm interrupt**
   POST /v1/chat 发送简历+优化诉求，接收 SSE token 流，结束时收到 interrupt 事件。

2. **Command resume → step_confirm 循环**
   再次 POST（同 thread_id，message="approve"），接收 token + interrupt 事件，
   循环批准直到无 interrupt。

3. **最终收尾**
   最后一次 POST 收到 done 事件，含最终 AI 消息。

前置条件
--------
   FastAPI server 已启动：python -m uvicorn agent.server:app --port 8000
   （本脚本默认连 http://127.0.0.1:8123，可改 BASE_URL）

用法
----
   # 终端 1
   PYTHONPATH=src python -m uvicorn agent.server:app --port 8123
   # 终端 2
   python prototypes/phase5_sdk_test.py
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from typing import Any

import httpx

BASE_URL = os.environ.get("PHASE5_BASE_URL", "http://127.0.0.1:8123")


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("gbk", errors="replace").decode("gbk"))


async def stream_chat(
    client: httpx.AsyncClient, user_id: str, thread_id: str, message: str
) -> dict[str, Any]:
    """发起一次 /v1/chat SSE 请求，收集 token / interrupt / done 事件。

    Returns:
        ``{"tokens": str, "interrupts": list, "done": dict|None}``。
    """
    tokens: list[str] = []
    interrupts: list[Any] = []
    done: dict[str, Any] | None = None

    async with client.stream(
        "POST",
        f"{BASE_URL}/v1/chat",
        json={"user_id": user_id, "thread_id": thread_id, "message": message},
        timeout=httpx.Timeout(120.0, connect=10.0),
    ) as resp:
        if resp.status_code != 200:
            body = await resp.aread()
            raise RuntimeError(f"HTTP {resp.status_code}: {body[:300]!r}")
        event_type = ""
        data_buf: list[str] = []
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                event_type = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_buf.append(line.split(":", 1)[1].strip())
            elif line == "":
                # 事件边界
                if event_type and data_buf:
                    data = "\n".join(data_buf)
                    if event_type == "token":
                        tokens.append(data)
                    elif event_type == "interrupt":
                        try:
                            interrupts.append(json.loads(data))
                        except json.JSONDecodeError:
                            interrupts.append(data)
                    elif event_type == "done":
                        try:
                            done = json.loads(data)
                        except json.JSONDecodeError:
                            done = {"raw": data}
                event_type = ""
                data_buf = []

    return {
        "tokens": "".join(tokens),
        "interrupts": interrupts,
        "done": done,
    }


async def run() -> None:
    _safe_print("=" * 70)
    _safe_print("  Phase 5 SDK 端到端测试 — FastAPI /v1/chat SSE")
    _safe_print(f"  BASE_URL: {BASE_URL}")
    _safe_print("=" * 70)

    user_id = "sdk_test_user"
    thread_id = str(uuid.uuid4())

    resume_text = (
        "# 李四的简历\n"
        "## 教育背景\n某大学 软件工程 本科\n"
        "## 技能\nPython Java C++ Go Rust JavaScript TypeScript\n"
    )

    async with httpx.AsyncClient() as client:
        # ---- Turn 1: 激活 ----
        _safe_print("\n--- Turn 1: 发送简历 + 优化诉求 ---")
        r1 = await stream_chat(
            client,
            user_id,
            thread_id,
            f"帮我优化这份简历：\n{resume_text}\n我想补充项目经历，精简技能列表",
        )
        _safe_print(f"  tokens: {r1['tokens'][:120]}...")
        _safe_print(f"  interrupts: {len(r1['interrupts'])}")
        if r1["interrupts"]:
            intr = r1["interrupts"][-1]
            payload = intr.get("interrupts", [{}])[-1] if isinstance(intr, dict) else intr
            phase = payload.get("phase") if isinstance(payload, dict) else payload
            _safe_print(f"  interrupt phase: {phase}")
            if isinstance(payload, dict) and "plan" in payload:
                _safe_print(f"  plan: {payload['plan']}")

        # ---- 循环批准 ----
        turn = 2
        while r1["interrupts"] and turn < 12:
            _safe_print(f"\n--- Turn {turn}: 发送 approve ---")
            r1 = await stream_chat(client, user_id, thread_id, "approve")
            _safe_print(f"  tokens: {r1['tokens'][:120]}...")
            _safe_print(f"  interrupts: {len(r1['interrupts'])}")
            if r1["interrupts"]:
                intr = r1["interrupts"][-1]
                payload = intr.get("interrupts", [{}])[-1] if isinstance(intr, dict) else intr
                phase = payload.get("phase") if isinstance(payload, dict) else payload
                _safe_print(f"  interrupt phase: {phase}")
            if r1["done"]:
                _safe_print(f"  done: {str(r1['done'])[:200]}")
            turn += 1

        _safe_print("\n" + "=" * 70)
        _safe_print("  Phase 5 SDK 端到端测试完成（请人工核验 SSE 流内容）")
        _safe_print("=" * 70)


if __name__ == "__main__":
    import asyncio

    asyncio.run(run())
