"""Phase 2 索引重建脚本。

删除旧 Chroma 向量库（bge-small-zh 产出，与新 SiliconFlow bge-large-zh-v1.5
不兼容），调用 index_agent 重新灌库，并更新 index.md（LLM 维护，AP 调用会扣费）。

运行：
    .venv/Scripts/python.exe prototypes/phase2_rebuild_index.py
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHROMA_PATH = PROJECT_ROOT / "data" / "chroma"
TARGET_FILES = ["langgraph_state.md", "langgraph_subgraph.md"]


async def main() -> None:
    # 1) 删除旧 Chroma
    if CHROMA_PATH.exists():
        shutil.rmtree(CHROMA_PATH)
        print(f"[rebuild] 已删除 {CHROMA_PATH}")
    else:
        print(f"[rebuild] {CHROMA_PATH} 不存在，跳过删除")

    # 2) 调用 index_agent 重新灌库（会调 DeepSeek LLM 更新 index.md）
    from index_agent.graph import graph

    result = await graph.ainvoke({"target_files": TARGET_FILES})
    n_chunks = len(result.get("chunks", []))
    n_rows = len(result.get("existing_rows", []))
    print(f"[rebuild] 索引重建完成：{n_chunks} chunk 灌入，{n_rows} 条索引行")

    # 3) 验证 Chroma 条数
    from chromadb import PersistentClient

    client = PersistentClient(path=str(CHROMA_PATH))
    col = client.get_or_create_collection("knowledge_base")
    print(f"[rebuild] Chroma collection 向量条数: {col.count()}")


if __name__ == "__main__":
    asyncio.run(main())
