"""Phase 5 索引增量重建脚本。

把新增的 resume_template.md 通过 index_agent 重新灌库（切片→向量→LLM 索引），
不删除已有向量（index_agent 按 chunk_id 去重 upsert）。

运行：
    .venv/Scripts/python.exe prototypes/phase5_rebuild_index.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

TARGET_FILES = ["resume_template.md"]


async def main() -> None:
    from index_agent.graph import graph

    # 校验文件存在
    for f in TARGET_FILES:
        fp = PROJECT_ROOT / "data" / "markdown" / f
        if not fp.exists():
            print(f"[rebuild] 缺失文件: {fp}")
            return

    result = await graph.ainvoke({"target_files": TARGET_FILES})
    n_chunks = len(result.get("chunks", []))
    print(f"[rebuild] 增量索引完成：新增 {n_chunks} chunk")

    from chromadb import PersistentClient

    client = PersistentClient(path=str(PROJECT_ROOT / "data" / "chroma"))
    col = client.get_or_create_collection("knowledge_base")
    print(f"[rebuild] Chroma collection 总向量数: {col.count()}")


if __name__ == "__main__":
    asyncio.run(main())
