"""Phase 3 主图总线 SDK 测试脚本 (v2 — as_tool 路由版)。

连本地 LangGraph Studio（``langraph dev``，默认 127.0.0.1:2024），
通过 SDK 调起 ``main_agent_proto`` 图，验证端到端流程。

前置：在项目根执行 ``langraph dev`` 启动 server。

运行（项目根）：
    uv run python prototypes/phase3_sdk_test.py
"""

from __future__ import annotations

import asyncio
import uuid

from langgraph_sdk import get_client

STUDIO_URL = "http://127.0.0.1:2024"
GRAPH_ID = "main_agent"


def _summarize_trace(messages: list[dict]) -> list[dict]:
    """从消息列表中提取人类可读的决策轨迹。"""
    trace = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("type", "")
        content = (msg.get("content") or "")[:80]

        if role == "ai":
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                trace.append({
                    "role": "ai",
                    "action": f"调用工具: {tool_calls[0]['name']}",
                    "args": tool_calls[0].get("args", {}),
                })
            else:
                trace.append({
                    "role": "ai",
                    "action": "直接回答",
                    "preview": content,
                })
        elif role == "tool":
            trace.append({
                "role": "tool",
                "tool_call_id": msg.get("tool_call_id", "")[:12],
                "preview": content[:60],
            })
    return trace


async def run_conversation(
    client, thread_id: str, assistant_id: str, user_message: str
) -> dict:
    """通过 SDK 发一条消息到主图，返回最终 state。

    Args:
        client: LangGraph SDK 客户端。
        thread_id: 会话线程 ID。
        assistant_id: Assistant ID。
        user_message: 用户消息文本。

    Returns:
        最终消息列表、引用、决策轨迹。
    """

    input_ = {"messages": [{"role": "human", "content": user_message}]}
    final_state = None

    async for chunk in client.runs.stream(
        thread_id,
        assistant_id,
        input=input_,
        stream_mode="values",
    ):
        if chunk.event == "values":
            final_state = chunk.data

    assert final_state is not None, "未收到最终 state"

    all_messages = final_state.get("messages", [])
    trace = _summarize_trace(all_messages)

    # 最后一条 AI 回复（无 tool_call 的即为最终回答）
    final_reply = ""
    for msg in reversed(all_messages):
        if isinstance(msg, dict) and msg.get("type") == "ai":
            if not msg.get("tool_calls"):
                final_reply = msg.get("content", "")
                break

    return {
        "final_reply": final_reply,
        "citations": final_state.get("citations", []),
        "trace": trace,
        "messages_count": len(all_messages),
    }


async def main() -> None:
    print(f"=== Phase 3 主图总线 SDK 测试 (v2, {GRAPH_ID}) ===\n")

    client = get_client(url=STUDIO_URL)
    assistants = await client.assistants.search()
    instance = next(a for a in assistants if a["graph_id"] == GRAPH_ID)
    assistant_id = instance["assistant_id"]

    # 三个场景共用同一个 thread（同一会话）
    thread_id = str(uuid.uuid4())
    await client.threads.create(thread_id=thread_id, if_exists="do_nothing")

    fn = lambda msg: run_conversation(client, thread_id, assistant_id, msg)

    # ── 场景 A: 寒暄（无工具调用）─────────────────────────────────────
    print("--- [场景 A] 寒暄: '你好' ---")
    ret = await fn("你好")
    print(f"  回复: {ret['final_reply'][:120]}")
    print(f"  轨迹: {ret['trace']}")
    trace_actions = [t["action"] for t in ret["trace"]]
    assert any("直接回答" in a for a in trace_actions), "场景 A：应有直接回答"
    print("  ✓ 通过\n")

    # ── 场景 B: sub_agent + semantic 检索 ─────────────────────────────
    print("--- [场景 B] semantic 检索: '什么是 StateGraph？' ---")
    ret = await fn("什么是 StateGraph？")
    print(f"  回复: {ret['final_reply'][:200]}")
    print(f"  引用: {len(ret['citations'])} 条")
    for i, c in enumerate(ret["citations"][:3], 1):
        print(f"    #{i} [{c['file_path']}](L{c['start_line']}~{c['end_line']})")
    print(f"  轨迹:")
    for t in ret["trace"]:
        print(f"    {t}")
    # 语义检索可能命中也可能 gap，但不应是空消息
    assert ret["final_reply"], "场景 B：应有回复"
    assert any("rag_agent" in t["action"] for t in ret["trace"]
               if "action" in t), "场景 B：应触发 rag_agent 工具"
    print("  ✓ 通过\n")

    # ── 场景 C: sub_agent + keyword 检索 ──────────────────────────────
    print("--- [场景 C] keyword 检索: '查找 add_messages 相关内容' ---")
    ret = await fn("查找 add_messages 相关内容")
    print(f"  回复: {ret['final_reply'][:200]}")
    print(f"  引用: {len(ret['citations'])} 条")
    for i, c in enumerate(ret["citations"][:3], 1):
        print(f"    #{i} [{c['file_path']}](L{c['start_line']}~{c['end_line']})")
    print(f"  轨迹:")
    for t in ret["trace"]:
        print(f"    {t}")
    assert ret["final_reply"], "场景 C：应有回复"
    assert any("rag_agent" in t["action"] for t in ret["trace"]
               if "action" in t), "场景 C：应触发 rag_agent 工具"
    print("  ✓ 通过\n")

    print("=== [phase3_sdk_test] 三场景全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
