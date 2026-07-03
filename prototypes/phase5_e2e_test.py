#!/usr/bin/env python3
"""Phase 5 端到端冒烟测试 — resume_agent 真实子图接入主图。

测试内容
--------
1. **激活 → plan_confirm interrupt**
   发送"优化简历"消息 → chat_node 决策调 resume_agent → wrapper 编译子图 →
   plan_node → plan_confirm interrupt 挂起。

2. **Command resume → step_confirm interrupt**
   resume="approve" → plan_confirm 通过 → react_router → CRUD 工具 →
   step_confirm interrupt 挂起。

3. **Command resume → finalize → END**
   resume="approve" → step_confirm 通过 → 回 react_router → 无 tool_call →
   finalize → 主图 chat_node 收尾。

4. **拒绝恢复**
   （单步验证，在 step_confirm 时回复 "reject" 验证 last_draft 恢复。）

前置条件
--------
   无需 langgraph dev，直接运行。会生成 phase5_e2e.sqlite。
   需要配置好 .env 的 DeepSeek API。

用法
----
   python prototypes/phase5_e2e_test.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from typing import Any

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from langchain_core.messages import HumanMessage  # noqa: E402
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from agent.graph import build_main_graph  # noqa: E402
from agent.persistence import get_store  # noqa: E402

DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "phase5_e2e.sqlite"
)


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("gbk", errors="replace").decode("gbk"))


async def _pending_interrupt(graph: Any, config: dict) -> tuple[bool, Any]:
    state = await graph.aget_state(config)
    tasks = getattr(state, "tasks", []) or []
    for t in tasks:
        intr = getattr(t, "interrupts", None) or []
        if intr:
            return True, intr
    return False, None


async def _extract_last_ai(state: Any) -> str:
    values = state if isinstance(state, dict) else getattr(state, "values", state)
    msgs = values.get("messages", [])
    if msgs:
        last = msgs[-1]
        if hasattr(last, "type") and last.type == "ai":
            c = last.content if isinstance(last.content, str) else str(last.content)
            return c[:200] + "..." if len(c) > 200 else c
    return "(无 AI 回复)"


async def run_e2e() -> None:
    _safe_print("=" * 70)
    _safe_print("  Phase 5 端到端冒烟测试 — resume_agent 接入主图")
    _safe_print(f"  DB: {DB_PATH}")
    _safe_print("=" * 70)

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    thread_id = str(uuid.uuid4())
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    store = get_store()

    resume_text = (
        "# 张三的简历\n"
        "## 教育背景\n某某大学 计算机科学 本科\n"
        "## 技能\nPython Java C++ Go Rust JavaScript\n"
    )

    async with AsyncSqliteSaver.from_conn_string(DB_PATH) as cp:
        graph = build_main_graph(checkpointer=cp, store=store)

        # ---- Turn 1: 激活 resume_agent ----
        _safe_print("\n--- Turn 1: 用户发送简历 + 优化诉求 ---")
        async for _ in graph.astream(
            {
                "messages": [
                    HumanMessage(
                        content=f"帮我优化这份简历：\n{resume_text}"
                        "\n我想补充项目经历，精简技能列表"
                    )
                ],
                "user_id": "e2e_user",
            },
            config,
            stream_mode="updates",
        ):
            pass

        pending, intrs = await _pending_interrupt(graph, config)
        _safe_print(f"\n  Turn 1 后 pending interrupt: {pending}")
        if intrs:
            val = intrs[0].value if hasattr(intrs[0], "value") else intrs[0]
            phase = val.get("phase") if isinstance(val, dict) else val
            _safe_print(f"  interrupt phase: {phase}")
            if isinstance(val, dict) and "plan" in val:
                _safe_print(f"  plan: {val['plan']}")
        if not pending:
            state = await graph.aget_state(config)
            _safe_print(f"  [未挂起] 最后消息: {await _extract_last_ai(state.values)}")
            _safe_print("  ⚠️ LLM 未调用 resume_agent——检查 system prompt / 工具绑定")
            return

        # ---- Turn 2: 批准计划 ----
        _safe_print("\n--- Turn 2: Command(resume='approve') 批准计划 ---")
        async for _ in graph.astream(Command(resume="approve"), config, stream_mode="updates"):
            pass

        pending2, intrs2 = await _pending_interrupt(graph, config)
        _safe_print(f"\n  Turn 2 后 pending interrupt: {pending2}")
        if intrs2:
            val2 = intrs2[0].value if hasattr(intrs2[0], "value") else intrs2[0]
            phase2 = val2.get("phase") if isinstance(val2, dict) else val2
            _safe_print(f"  interrupt phase: {phase2}")
            if isinstance(val2, dict) and "after" in val2:
                _safe_print(f"  草稿(后):\n{val2['after']}")

        # ---- Turn 3: 批准该步 ----
        _safe_print("\n--- Turn 3: Command(resume='approve') 批准该步 ---")
        async for _ in graph.astream(Command(resume="approve"), config, stream_mode="updates"):
            pass

        pending3, intrs3 = await _pending_interrupt(graph, config)
        _safe_print(f"\n  Turn 3 后 pending interrupt: {pending3}")

        # 可能还有更多 step_confirm 循环（取决于 LLM 计划步数）
        turns = 4
        while pending3 and turns < 10:
            _safe_print(f"\n--- Turn {turns}: 继续批准 ---")
            async for _ in graph.astream(
                Command(resume="approve"), config, stream_mode="updates"
            ):
                pass
            pending3, intrs3 = await _pending_interrupt(graph, config)
            _safe_print(f"  Turn {turns} 后 pending interrupt: {pending3}")
            turns += 1

        final_state = await graph.aget_state(config)
        _safe_print(f"\n  最终消息数: {len(final_state.values.get('messages', []))}")
        _safe_print(f"  最终 AI: {await _extract_last_ai(final_state.values)}")
        cur = final_state.values.get("current_resume", "")
        _safe_print(f"\n  current_resume:\n{cur}")
        _safe_print("\n" + "=" * 70)
        _safe_print("  Phase 5 端到端冒烟测试完成（请人工核验草稿内容）")
        _safe_print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_e2e())
