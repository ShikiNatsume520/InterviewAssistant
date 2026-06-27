"""Phase 2 RAG Agent SDK 测试脚本。

仿照 phase1_sdk_test.py 的逻辑：连本地 Studio → search 出 graph_id=rag_agent
的 assistant → create thread → runs.stream 跑图，验证双管道检索 + gap 判定。

前置：先在项目根执行 ``langgraph dev`` 启动 server（默认 127.0.0.1:2024）。

运行（在项目根）：
    uv run python prototypes/phase2_sdk_test.py
"""

from __future__ import annotations

import asyncio
import uuid

from langgraph_sdk import get_client

STUDIO_URL = "http://127.0.0.1:2024"
"""本地 LangGraph Studio server 地址（langgraph dev 默认端口）。"""


async def run_pipeline(
    search_query: str, search_type: str
) -> dict:
    """通过 SDK 调用 rag_agent 子图并返回最终 state。

    Args:
        search_query: 检索词。
        search_type: ``"semantic"`` 或 ``"keyword"``。

    Returns:
        最终 state 中的关键字段（citations_output / gap_topic / raw_results）。
    """
    client = get_client(url=STUDIO_URL)
    assistants = await client.assistants.search()
    instance = next(a for a in assistants if a["graph_id"] == "rag_agent")
    assistant_id = instance["assistant_id"]

    test_uuid = str(uuid.uuid4())
    thread = await client.threads.create(
        thread_id=test_uuid, if_exists="do_nothing"
    )
    thread_id = thread["thread_id"]

    input_ = {
        "search_query": search_query,
        "search_type": search_type,
    }
    final_values = None
    async for chunk in client.runs.stream(
        thread_id, assistant_id, input=input_, stream_mode="values"
    ):
        if chunk.event == "values":
            final_values = chunk.data

    assert final_values is not None, "未收到最终 state"
    return {
        "citations": final_values.get("citations_output", []),
        "gap_topic": final_values.get("gap_topic"),
        "raw_count": len(final_values.get("raw_results", [])),
    }


async def main() -> None:
    """SDK 调用 rag_agent 验证三个核心场景。"""
    print("=== Phase 2 RAG Agent SDK 测试 ===\n")

    # 1) semantic 管道：相关查询
    print("--- [semantic] 相关查询: '状态累加器 add_messages' ---")
    ret = await run_pipeline("状态累加器 add_messages", "semantic")
    print(f"  citations: {len(ret['citations'])} 条, gap_topic: {ret['gap_topic']}")
    for i, c in enumerate(ret["citations"][:3], 1):
        print(f"  #{i} [{c['file_path']}]({c['start_line']}~{c['end_line']}) {c['content'][:40]}")
    assert len(ret["citations"]) > 0 and ret["gap_topic"] is None

    # 2) semantic 管道：gap 场景
    print("\n--- [semantic] gap 场景: '量子计算原理' ---")
    ret = await run_pipeline("量子计算原理", "semantic")
    print(f"  citations: {len(ret['citations'])} 条, gap_topic: {ret['gap_topic']}")
    assert len(ret["citations"]) == 0 and ret["gap_topic"] == "量子计算原理"

    # 3) keyword 管道：精确匹配
    print("\n--- [keyword] 精确匹配: 'add_messages' ---")
    ret = await run_pipeline("add_messages", "keyword")
    print(f"  citations: {len(ret['citations'])} 条, gap_topic: {ret['gap_topic']}")
    for i, c in enumerate(ret["citations"], 1):
        print(f"  #{i} [{c['file_path']}]({c['start_line']}~{c['end_line']}) {c['content'][:40]}")
    assert len(ret["citations"]) > 0 and ret["gap_topic"] is None

    print("\n[phase2_sdk_test] OK: 三场景全部通过")


if __name__ == "__main__":
    asyncio.run(main())
