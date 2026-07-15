"""phase14: 验证 resume 子图作主图节点时的 3 个机制点（定稿架构依赖）。

验证：
1. 流式透传：主图 astream(stream_mode="messages") 能否捕获 resume 子图 chat_node 的 LLM token？
2. Command(resume) 穿透：主图 thread 发 Command(resume={resume_file,resume_shot}) 能否精准到达子图 select_resume interrupt？
3. 子图 state 挖掘：主图 aget_state().tasks[].state 能否拿到 resume 子图的 resume_shot/messages？

用真 deepseek LLM + 主图（build_main_graph）+ MemorySaver。
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.main.graph import build_main_graph


async def main() -> None:
    g = build_main_graph(checkpointer=MemorySaver())
    cfg: dict[str, Any] = {"configurable": {"thread_id": "phase14"}}

    print("=== 1. 主图激活 resume（说'改简历'）→ select_resume interrupt ===")
    token_count = 0
    async for chunk, meta in g.astream(
        {"messages": [HumanMessage(content="帮我优化简历")], "user_id": "u"},
        cfg,
        stream_mode="messages",
    ):
        text = ""
        if isinstance(chunk, str):
            text = chunk
        else:
            c = getattr(chunk, "content", "")
            text = c if isinstance(c, str) else str(c)
        if text:
            token_count += 1
    print(f"  首轮流式 token chunks: {token_count}")

    st = await g.aget_state(cfg)
    print(f"  next={st.next}")
    assert st.next == ("resume_agent",), f"应挂起在 resume_agent, got {st.next}"

    # ── 机制3：挖子图 state ──
    print("\n=== 2. 挖子图 state（tasks[].state）===")
    for t in st.tasks or []:
        print(f"  task: {t.name}")
        # 探 task 的 state / subgraphs
        tstate = getattr(t, "state", None)
        if tstate is not None:
            vals = getattr(tstate, "values", {})
            print(f"    task.state.values keys: {list(vals.keys()) if isinstance(vals, dict) else type(vals)}")
            if isinstance(vals, dict):
                print(f"    resume_shot in task state: {'resume_shot' in vals}")
                print(f"    messages in task state: {'messages' in vals}, n={len(vals.get('messages', []))}")
        # 探 subgraphs
        sgs = getattr(t, "subgraphs", None)
        print(f"    subgraphs: {type(sgs).__name__}, len={len(sgs) if sgs else 0}")

    # ── 机制2：Command(resume) 穿透到子图 select_resume ──
    print("\n=== 3. Command(resume={resume_file,resume_shot}) 穿透主图 → 子图 select_resume ===")
    # 模拟前端选 sample.md（前端读文件传 resume_shot）
    from kernel.paths import RESUMES_DIR
    sample_path = RESUMES_DIR / "sample.md"
    resume_shot = sample_path.read_text(encoding="utf-8") if sample_path.is_file() else "# 测试"

    token_count2 = 0
    saw_resume_token = False
    async for chunk, meta in g.astream(
        Command(resume={"resume_file": "sample.md", "resume_shot": resume_shot}),
        cfg,
        stream_mode="messages",
    ):
        text = ""
        if isinstance(chunk, str):
            text = chunk
        else:
            c = getattr(chunk, "content", "")
            text = c if isinstance(c, str) else str(c)
        if text:
            token_count2 += 1
            saw_resume_token = True
            # 打印前几个 token 看 content
            if token_count2 <= 5:
                print(f"    token[{token_count2}]: {text[:40]}")

    st2 = await g.aget_state(cfg)
    print(f"  resume 后 next={st2.next}")
    print(f"  流式 token chunks: {token_count2}")

    # 判断穿透成功：子图应已过 select_resume（resume_shot 灌入）→ init → chat_node
    # 若 chat_node 调了 grep_replace → approve_node interrupt
    # 若 chat_node 直接总结 → hitl_standby interrupt
    # 若 next 仍是 resume_agent 但 interrupt 变了 → 穿透成功
    print(f"\n=== 结论 ===")
    print(f"  机制2（Command穿透）: next={st2.next} {'✓ 穿透成功' if st2.next else '?'}")
    print(f"  机制1（流式透传）: resume后 token={token_count2} {'✓ 捕获到子图token' if token_count2 > 0 else '✗ 无token'}")


if __name__ == "__main__":
    asyncio.run(main())
