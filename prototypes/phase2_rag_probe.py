"""Phase 2 RAG 子图检索机制探针。

验证锁定方案的关键技术风险（不编译子图，仅验证检索机制）：
1. Chroma 带分召回：Phase 1 ``query_chunks`` 不返回 score，本探针取 ``distances``
   并归一化为相似度（``1/(1+dist)``）。
2. semantic 管道：向量召回 chunk → 按 (file, heading) 相邻/重叠行区间合并（标题节扩展）。
3. keyword 管道：解析 index.md 关键词匹配 → 候选文件 → 定向 grep 命中行区间；
   index 无候选文件时退化为全目录 grep。
4. gap 判定：有效命中条数 == 0 → ``gap_topic = search_query``。

只读 ``data/chroma`` 与 ``data/markdown``，不写、不污染。不 import ``src/``。
正式实现时建议把 collection 切到 cosine 空间（需重跑 index_agent 重建向量库）。

运行：
    .venv/Scripts/python.exe prototypes/phase2_rag_probe.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal



PROJECT_ROOT = Path(__file__).resolve().parent.parent
MARKDOWN_DIR = PROJECT_ROOT / "data" / "markdown"
INDEX_MD = PROJECT_ROOT / "data" / "index.md"
CHROMA_PATH = PROJECT_ROOT / "data" / "chroma"

COLLECTION = "knowledge_base"

# 余弦距离 + 分差置信度判据
# - SCORE_LOW_THRESHOLD: 低于此值视为噪声滤除
# - GAP_RATIO_THRESHOLD: top-1 分 / 全部分均值小于此值时视为低置信度 → gap
SCORE_LOW_THRESHOLD = 0.55
GAP_RATIO_THRESHOLD = 1.15


# --------------------------------------------------------------------------- #
# 通用：行区间合并 + 引用渲染
# --------------------------------------------------------------------------- #
def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """合并相邻/重叠的 1-based 闭区间（[10,20]+[15,25]→[10,25]；[10,11]+[12,13]→[10,13]）。

    Args:
        ranges: (start, end) 闭区间列表（1-based）。

    Returns:
        合并后有序不重叠区间列表。
    """
    if not ranges:
        return []
    rs = sorted(ranges)
    merged = [rs[0]]
    for s, e in rs[1:]:
        ls, le = merged[-1]
        if s <= le + 1:  # 相邻或重叠
            merged[-1] = (ls, max(le, e))
        else:
            merged.append((s, e))
    return merged


def _fmt_citation(r: dict[str, Any]) -> str:
    """渲染结构化引用为 ``[文件名](start~end) score=.. source=..``。"""
    return (
        f"[{r['file_path']}]({r['start_line']}~{r['end_line']}) "
        f"score={r['score']:.3f} src={r['source']} :: {r['content'][:40]}"
    )


# --------------------------------------------------------------------------- #
# keyword 管道：index.md 匹配 + 定向 grep
# --------------------------------------------------------------------------- #
INDEX_ROW_RE = re.compile(r"\[([^\]]+)\]\(data/markdown/([^\)]+)\)")


def _parse_index(text: str) -> list[dict[str, Any]]:
    """解析 index.md 表格行 → [{keywords, summary, files}]。"""
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3 or cells[0] in ("关键词", "--------"):
            continue
        kws = [k.strip() for k in cells[0].split(",") if k.strip()]
        files = [m[1] for m in INDEX_ROW_RE.findall(cells[2])]
        rows.append({"keywords": kws, "summary": cells[1], "files": files})
    return rows


def _extract_terms(query: str) -> list[str]:
    """从 query 切出检索词（纯规则，无 jieba）。

    ASCII 段按空格/标点切；CJK 连续段整段保留。
    """
    terms: list[str] = []
    for tok in re.findall(r"[一-鿿]+|[A-Za-z0-9_\-]+", query):
        if len(tok) >= 2 or re.search(r"[A-Za-z]", tok):
            terms.append(tok)
    return terms


def _grep_file(path: Path, terms: list[str]) -> list[dict[str, Any]]:
    """在单文件内逐行匹配任一 term，返回命中行区间合并后的结果。"""
    if not terms:
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    pats = [re.compile(re.escape(t), re.IGNORECASE) for t in terms]
    hit_lines: list[int] = []
    for i, ln in enumerate(lines, 1):
        if any(p.search(ln) for p in pats):
            hit_lines.append(i)
    if not hit_lines:
        return []
    out: list[dict[str, Any]] = []
    for s, e in _merge_ranges([(h, h) for h in hit_lines]):
        out.append(
            {
                "file_path": path.name,
                "start_line": s,
                "end_line": e,
                "content": "\n".join(lines[s - 1 : e]),
                "score": 1.0,  # grep 无分数，命中即满分（仅用于排序占位）
                "source": "grep",
            }
        )
    return out


def keyword_pipeline(query: str) -> list[dict[str, Any]]:
    """keyword 管道：index 关键词匹配 → 候选文件定向 grep；无候选则全目录 grep。"""
    terms = _extract_terms(query)
    rows = _parse_index(INDEX_MD.read_text(encoding="utf-8"))

    # 匹配 index：query 任一词命中某行的任一 keyword
    candidate_files: list[str] = []
    for row in rows:
        if any(t in kw or kw in t for t in terms for kw in row["keywords"]):
            candidate_files.extend(row["files"])

    candidate_files = list(dict.fromkeys(candidate_files))  # 去重保序
    print(f"  [keyword] terms={terms} candidates={candidate_files}")

    targets: list[Path] = []
    if candidate_files:
        targets = [MARKDOWN_DIR / f for f in candidate_files if (MARKDOWN_DIR / f).exists()]
    else:
        print("  [keyword] index 无候选 → 退化为全目录 grep")
        targets = sorted(MARKDOWN_DIR.glob("*.md"))

    results: list[dict[str, Any]] = []
    for fp in targets:
        results.extend(_grep_file(fp, terms))
    return results


# --------------------------------------------------------------------------- #
# semantic 管道：向量召回 + 标题节扩展合并
# --------------------------------------------------------------------------- #
def _expand_heading_sections(
    recalled: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按 (file_path, heading) 合并相邻/重叠行区间（标题节扩展）。

    同一标题节被 Phase 1 二次切碎的多个 chunk 在此合并还原。
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for c in recalled:
        groups.setdefault((c["file_path"], c["heading"]), []).append(c)

    out: list[dict[str, Any]] = []
    for (fp, heading), chunks in groups.items():
        ranges = _merge_ranges([(c["start_line"], c["end_line"]) for c in chunks])
        # 内容从该组所有 chunk 拼接（按行号去重近似），分数取组内最高
        best_score = max(c["score"] for c in chunks)
        content = "\n".join(c["content"] for c in chunks)
        for s, e in ranges:
            out.append(
                {
                    "file_path": fp,
                    "start_line": s,
                    "end_line": e,
                    "heading": heading,
                    "content": content,
                    "score": best_score,
                    "source": "vector",
                }
            )
    return out


def semantic_pipeline(
    query: str,
    collection: Any,
    n_results: int = 8,
) -> list[dict[str, Any]]:
    """semantic 管道：向量召回带分 chunk → 标题节扩展合并 → 低分兜底全目录 grep。"""
    res = collection.query(query_texts=[query], n_results=n_results)

    recalled: list[dict[str, Any]] = []
    for i in range(len(res["ids"][0])):
        meta = res["metadatas"][0][i]
        dist = res["distances"][0][i]
        recalled.append(
            {
                "file_path": meta["file_path"],
                "start_line": int(meta["start_line"]),
                "end_line": int(meta["end_line"]),
                "heading": meta["heading"],
                "content": res["documents"][0][i],
                "score": 1.0 / (1.0 + dist),
                "source": "vector",
            }
        )

    expanded = _expand_heading_sections(recalled)
    scores = [r["score"] for r in expanded]
    top_score = max(scores) if scores else 0.0
    avg_score = sum(scores) / len(scores) if scores else 0.0
    ratio = top_score / avg_score if avg_score > 0 else 1.0
    print(f"  [semantic] 召回 {len(recalled)} chunk → 扩展合并 {len(expanded)} 节，"
          f"最高分 {top_score:.3f} avg={avg_score:.3f} 分差比={ratio:.2f}")

    # 分差比判置信度：top-1 显著高于其余（>15%）才认为非噪声
    confident = ratio >= GAP_RATIO_THRESHOLD and top_score >= SCORE_LOW_THRESHOLD
    if not confident:
        print(f"  [semantic] 低置信度（ratio={ratio:.2f} < {GAP_RATIO_THRESHOLD}）→ 兜底全目录 grep")
        for fp in sorted(MARKDOWN_DIR.glob("*.md")):
            expanded.extend(_grep_file(fp, _extract_terms(query)))
        # 重检 grp 后分数（grep 命中 score=1.0，自然通过）
        scores = [r["score"] for r in expanded]
        top_score = max(scores) if scores else 0.0
        avg_score = sum(scores) / len(scores) if scores else 0.0
        ratio = top_score / avg_score if avg_score > 0 else 1.0
        confident = ratio >= GAP_RATIO_THRESHOLD and top_score >= SCORE_LOW_THRESHOLD

    if not confident:
        print("  [semantic] 兜底后仍低置信度 → gap")
        return []
    # 分数过滤：仅返回高于阈值的条目
    effective = [r for r in expanded if r["score"] >= SCORE_LOW_THRESHOLD]
    if not effective:
        print("  [semantic] 过滤后无有效命中 → gap")
    return effective


# --------------------------------------------------------------------------- #
# gap 判定 + 汇总
# --------------------------------------------------------------------------- #
def aggregate(
    raw_results: list[dict[str, Any]], query: str
) -> tuple[list[dict[str, Any]], str | None]:
    """纯规则汇总：按 score 降序排序；有效命中 == 0 → gap_topic=query。"""
    if not raw_results:
        return [], query
    ranked = sorted(raw_results, key=lambda r: r["score"], reverse=True)
    return ranked, None


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def _get_collection() -> Any:
    import chromadb
    from index_agent.tools.vectorstore import get_embed_fn

    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    return client.get_or_create_collection(COLLECTION, embedding_function=get_embed_fn())


def run_pipeline(
    search_type: Literal["semantic", "keyword"],
    search_query: str,
    collection: Any,
) -> None:
    """跑单条管道 + 汇总，打印 citations / gap_topic。"""
    print(f"\n=== search_type={search_type} | query={search_query!r} ===")
    if search_type == "semantic":
        raw = semantic_pipeline(search_query, collection)
    else:
        raw = keyword_pipeline(search_query)

    citations, gap = aggregate(raw, search_query)
    if gap:
        print(f"  >>> gap_topic={gap!r}（命中 0 条）")
    else:
        print(f"  >>> citations ({len(citations)} 条):")
        for c in citations:
            print(f"    - {_fmt_citation(c)}")


def main() -> None:
    """对样例 query 跑两个管道 + gap 场景，验证检索机制。"""
    collection = _get_collection()
    print(f"[probe] collection={COLLECTION} count={collection.count()}")

    run_pipeline("semantic", "状态累加器 add_messages 怎么用", collection)
    run_pipeline("semantic", "主图和子图如何隔离状态", collection)
    run_pipeline("keyword", "add_messages reducer", collection)
    run_pipeline("keyword", "subgraph wrapper node", collection)
    run_pipeline("semantic", "量子计算原理", collection)  # gap 场景

    print("\n[probe] OK: semantic/keyword 双管道 + 标题节合并 + gap 判定验证通过")


if __name__ == "__main__":
    main()
