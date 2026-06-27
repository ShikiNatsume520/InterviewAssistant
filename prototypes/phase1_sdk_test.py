"""Phase 1 Index Agent SDK 测试脚本。

完全仿照 ref/sdk_test_ref.py 的逻辑：连本地 Studio → search 出 graph_id=index_agent
的 assistant → create thread → runs.stream 跑图。
checkpoint 由 Studio 持久化，可在 Studio 回看可视化轨迹。

前置：先在项目根执行 ``langgraph dev`` 启动 server（默认 127.0.0.1:2024）。

运行（在项目根）：
    .venv/Scripts/python.exe prototypes/phase1_sdk_test.py
"""

from __future__ import annotations

import asyncio
import uuid

from langgraph_sdk import get_client

STUDIO_URL = "http://127.0.0.1:2024"
"""本地 LangGraph Studio server 地址（langgraph dev 默认端口）。"""

TARGET_FILES = ["langgraph_state.md", "langgraph_subgraph.md"]
"""本批待 Index Agent 处理的 markdown 文件名列表。"""

QUERY = "主图和子图如何隔离状态"
"""跑完图后做向量检索验证用的查询文本。"""


async def main() -> None:
    """SDK 调用 Index Agent 处理样例 → 打印最终 state → 验证向量检索。

    步骤：
    1. 连本地 Studio。
    2. search 出 graph_id=index_agent 的 assistant。
    3. create thread。
    4. runs.stream 以 values 模式跑图，input={target_files: [...]}。
    5. 打印最终 state（含 index_update / chunks / existing_rows）。
    6. 跑完后再直查 Chroma 向量库，打印 query 召回的行级引用，验证灌库效果。
    """
    # 1) 连本地 Studio
    client = get_client(url=STUDIO_URL)

    # 2) search 出 graph_id=index_agent 的 assistant
    assistants = await client.assistants.search()
    instance = next(a for a in assistants if a["graph_id"] == "index_agent")
    assistant_id = instance["assistant_id"]
    print(f"--- 成功获取 Index Agent 实例 ID: {assistant_id} ---")

    # 3) 注册/创建线程
    test_uuid = str(uuid.uuid4())
    thread = await client.threads.create(thread_id=test_uuid, if_exists="do_nothing")
    thread_id = thread["thread_id"]
    print(f"--- 成功在 Studio 注册 Thread ID: {thread_id} ---")

    # 4) runs.stream 跑图
    print("\n--- [唤醒 Index Agent 处理样例] ---")
    input_ = {"target_files": TARGET_FILES}
    final_values = None
    async for chunk in client.runs.stream(
        thread_id, assistant_id, input=input_, stream_mode="values"
    ):
        if chunk.event == "values":
            final_values = chunk.data

    # 5) 打印最终 state
    assert final_values is not None, "未收到最终 state"
    print("\n=== 最终 state keys ===")
    print(list(final_values.keys()))

    update = final_values.get("index_update")
    assert update is not None, "最终 state 应含 index_update"
    rows = update["rows"]
    print(f"\n=== index.md 索引行（共 {len(rows)} 条）===")
    for i, r in enumerate(rows, 1):
        print(f"  {i}. 关键词: {r['keywords']}")
        print(f"     摘要: {r['summary']}")
        print(f"     文件: {r['files']}")

    chunks = final_values.get("chunks", [])
    print(f"\n=== 切片数: {len(chunks)} ===")
    for c in chunks:
        print(f"  {c['file_path']} L{c['start_line']}-{c['end_line']} [{c['heading']}]")

    # 6) 直查 Chroma 验证灌库效果（带行号引用）
    print(f"\n=== 向量检索验证 query: {QUERY} ===")
    from index_agent.graph import CHROMA_PATH
    from index_agent.tools.vectorstore import make_persistent_client, query_chunks

    chroma_client = make_persistent_client(CHROMA_PATH)
    hits = query_chunks(chroma_client, QUERY, n_results=3)
    for i, h in enumerate(hits, 1):
        print(f"  #{i} [{h['file_path']}]({h['start_line']}~{h['end_line']}) [{h['heading']}]")
        print(f"      {h['content'][:50]}")

    print("\n[phase1_sdk_test] OK: Index Agent SDK 调用 + 向量检索验证通过")


if __name__ == "__main__":
    asyncio.run(main())
