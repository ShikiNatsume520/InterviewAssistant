"""phase15: 重新验证主图 astream + subgraphs=True 能否捕获 resume 子图 chat_node token。

phase14 失败是因为没加 subgraphs=True。LangGraph 的 astream 加 subgraphs=True 后，
子图内部的 LLM token / 节点更新会作为嵌套流事件推送。

验证：
1. 主图激活 resume → select_resume interrupt（透传主图）
2. Command(resume={resume_file,resume_shot}) 恢复 → resume 子图 chat_node 调 LLM
3. 主图 astream(stream_mode="messages", subgraphs=True) 能否拿到 resume chat_node token？
4. 顺便验证 aget_state 能否拿到主图 state 里的 resume 子图相关字段（或通过 tasks 挖）
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.main.graph import build_main_graph
from kernel.paths import RESUMES_DIR


async def main() -> None:
    g = build_main_graph(checkpointer=MemorySaver())
    cfg: dict[str, Any] = {"configurable": {"thread_id": "phase15"}}

    print("=== 1. 主图激活 resume → select_resume interrupt ===")
    async for ev in g.astream(
        {"messages": [HumanMessage(content="帮我优化简历，把八爪科技大学改成测试科技大学")], "user_id": "u"},
        cfg,
        stream_mode="updates",
        subgraphs=True,
    ):
        # ev 形如 (namespace, chunk) 当 subgraphs=True
        if isinstance(ev, tuple) and len(ev) == 2:
            ns, chunk = ev
            ns_str = "|".join(str(n) for n in ns) if ns else "main"
            for node_name, node_data in (chunk.items() if isinstance(chunk, dict) else []):
                print(f"  [{ns_str}] {node_name}")
        else:
            print(f"  [?] {type(ev).__name__}")

    st = await g.aget_state(cfg)
    print(f"  next={st.next}")

    # 恢复 select_resume
    sample = RESUMES_DIR / "sample.md"
    shot = sample.read_text(encoding="utf-8") if sample.is_file() else "# x"

    print("\n=== 2. Command(resume) 恢复 + subgraphs=True 流式 ===")
    token_count = 0
    resume_token_count = 0
    async for ev in g.astream(
        Command(resume={"resume_file": "sample.md", "resume_shot": shot}),
        cfg,
        stream_mode="messages",
        subgraphs=True,
    ):
        # subgraphs=True 时 ev = (namespace, (chunk, metadata))
        if isinstance(ev, tuple) and len(ev) == 2:
            ns, payload = ev
            ns_str = "|".join(str(n) for n in ns) if ns else "main"
            chunk = payload[0] if isinstance(payload, tuple) and len(payload) >= 1 else payload
            text = ""
            if isinstance(chunk, str):
                text = chunk
            else:
                c = getattr(chunk, "content", "")
                text = c if isinstance(c, str) else str(c)
            if text:
                token_count += 1
                if ns:  # 非主图 namespace = 子图
                    resume_token_count += 1
                    if resume_token_count <= 8:
                        print(f"  [{ns_str}] token: {text[:50]}")

    print(f"\n=== 结论 ===")
    print(f"  总 token chunks: {token_count}")
    print(f"  resume 子图 token chunks: {resume_token_count}")
    print(f"  {'✓ subgraphs=True 能捕获子图 token' if resume_token_count > 0 else '✗ 仍拿不到子图 token'}")

    st2 = await g.aget_state(cfg)
    print(f"  最终 next={st2.next}")
    print(f"  主图 state keys: {list(st2.values.keys())}")


if __name__ == "__main__":
    asyncio.run(main())
