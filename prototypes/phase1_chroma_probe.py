"""Phase 1 向量检索探针：验证 Chroma 灌入 + bge-small-zh-v1.5 向量检索召回。

关键技术风险：
- 本地 ``bge-small-zh-v1.5`` 能正常加载并向量化中文 markdown（GPU 加速）。
- 切片 metadata 的 ``start_line``/``end_line``/``file_path`` 能随向量灌入 Chroma。
- query 召回的 top-k 能还原成 ``[文件名](start~end)`` 行级引用。

本探针用临时 Chroma 目录（``tempfile``），**不污染** ``data/chroma/``。
复用 ``phase1_chunk_probe.chunk_markdown`` 做切片（同目录 import）。

运行（首次会从 HF 下载 ~95MB 权重，慢可设 ``HF_ENDPOINT=https://hf-mirror.com``）：
    .venv/Scripts/python.exe prototypes/phase1_chroma_probe.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from phase1_chunk_probe import chunk_markdown
from sentence_transformers import SentenceTransformer

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "markdown"
MODEL_PATH = str(Path(__file__).resolve().parent.parent / "models" / "bge-small-zh-v1.5")
COLLECTION = "knowledge_base"
QUERIES = ["状态累加器 add_messages 怎么用", "主图和子图如何隔离状态"]


def _format_citation(meta: dict[str, Any]) -> str:
    """把 chunk metadata 渲染成行级引用 ``[文件名](start~end)``。"""
    fp = meta["file_path"]
    return f"[{fp}]({meta['start_line']}~{meta['end_line']})"


async def main() -> None:
    """切样例 → 向量化 → 灌 Chroma → query 召回 → 打印行级引用。"""
    import chromadb

    # 1) 切片
    chunks: list[dict[str, Any]] = []
    for fp in sorted(DATA_DIR.glob("*.md")):
        chunks.extend(chunk_markdown(fp.read_text(encoding="utf-8"), fp.name))
    print(f"[chroma_probe] 切片 {len(chunks)} 个 chunk")

    # 2) 加载本地 embedding 模型（GPU 自动启用）
    model = SentenceTransformer(MODEL_PATH)
    device = model.device
    print(f"[chroma_probe] 模型加载完成 device={device}")

    # 3) 向量化并灌入临时 Chroma
    docs = [c["content"] for c in chunks]
    embeddings = model.encode(docs, convert_to_numpy=True, show_progress_bar=False)
    metas = [
        {
            "file_path": c["file_path"],
            "start_line": c["start_line"],
            "end_line": c["end_line"],
            "heading": c["heading"],
        }
        for c in chunks
    ]
    ids = [f"{c['file_path']}:{c['start_line']}:{c['end_line']}" for c in chunks]

    # Windows 上 Chroma 后台线程会持有文件句柄，TemporaryDirectory 退出清理会报
    # WinError 32（不影响检索结果，仅清理失败）。用 ignore_cleanup_errors 容忍。
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        client = chromadb.PersistentClient(path=tmp)
        col = client.get_or_create_collection(COLLECTION)
        col.add(ids=ids, embeddings=embeddings.tolist(), documents=docs, metadatas=metas)
        print(f"[chroma_probe] 灌入 {col.count()} 条向量到 collection={COLLECTION}")

        # 4) query 召回
        for q in QUERIES:
            q_emb = model.encode([q], convert_to_numpy=True, show_progress_bar=False)
            res = col.query(query_embeddings=q_emb.tolist(), n_results=3)
            print(f"\n=== query: {q} ===")
            for i in range(len(res["ids"][0])):
                meta = res["metadatas"][0][i]
                dist = res["distances"][0][i]
                doc = res["documents"][0][i]
                print(f"  #{i + 1} {_format_citation(meta)} dist={dist:.3f}")
                print(f"       heading=[{meta['heading']}] {doc[:50]}")

        del res, col, client

    print("\n[chroma_probe] OK: 向量灌入 + 检索召回 + 行级引用渲染通过")


if __name__ == "__main__":
    asyncio.run(main())
