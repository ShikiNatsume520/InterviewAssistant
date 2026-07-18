"""检索管道：semantic / keyword 双管道 + 去重合并 + gap 判定。

三个对外接口：
- ``retrieve_pipeline(query, search_type)`` — 按类型分派并返回原始结果。
- ``aggregate_results(raw_results, query)`` — gap 判定 + 排序，返回最终输出。

共享常量 / 阈值：所有 Chroma、阈值、路径相关配置集中在本模块顶部，
便于调试阶段按需调整。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents.index.scope import KnowledgeScope, resolve_knowledge_scope
from agents.rag.state import Citation, RawResult
from kernel.knowledge import KnowledgeRepository
from kernel.logging import dlog
from kernel.persistence import APP_DB_PATH

if TYPE_CHECKING:
    from chromadb.api.types import QueryResult

# --------------------------------------------------------------------------- #
# 阈值常量
# --------------------------------------------------------------------------- #
SCORE_LOW_THRESHOLD = 0.55
"""余弦空间下：低于此值的向量得分视为噪声（score 过滤阈值）。"""

VECTOR_N_RESULTS = 8
"""向量检索 top-k 数（知识库仅 2 个文件时设为 8 足以覆盖全库）。"""

# --------------------------------------------------------------------------- #
# 通用工具
# --------------------------------------------------------------------------- #
INDEX_ROW_RE = re.compile(r"\[([^\]]+)\]\(data/markdown/([^\)]+)\)")
"""解析 index.md 引用列的 regex。"""


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """合并相邻 / 重叠的 1-based 闭区间。

    Args:
        ranges: (start, end) 闭区间列表。

    Returns:
        合并后有序不重叠区间列表。
    """
    if not ranges:
        return []
    rs = sorted(ranges)
    merged = [rs[0]]
    for s, e in rs[1:]:
        ls, le = merged[-1]
        if s <= le + 1:
            merged[-1] = (ls, max(le, e))
        else:
            merged.append((s, e))
    return merged


def _extract_terms(query: str) -> list[str]:
    """解析关键词查询，支持引号包裹的短语。

    示例: ' "StateGraph 状态管理" 检查点 "tool calling" '
          -> ['StateGraph 状态管理', '检查点', 'tool calling']
    """
    # 使用正则匹配引号内的内容，或者匹配不带引号的单词
    pattern = r'"([^"]*)"|(\S+)'
    matches = re.findall(pattern, query)

    terms = []
    for quoted, unquoted in matches:
        if quoted:
            terms.append(quoted)  # 保留引号内的完整短语
        elif unquoted:
            terms.append(unquoted)  # 保留独立关键词
    return terms


def _parse_index(text: str) -> list[dict[str, Any]]:
    """解析 index.md 表格行。

    Args:
        text: index.md 全文。

    Returns:
        [{keywords, summary, files}] 列表。
    """
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


# --------------------------------------------------------------------------- #
# grep 工具
# --------------------------------------------------------------------------- #
def _grep_file(
    path: Path,
    terms: list[str],
    *,
    scope: str = "public",
    resource_id: str | None = None,
    display_name: str | None = None,
) -> list[RawResult]:
    """在单文件内逐行匹配任一检索词，返回合并行区间后的结果。

    score 为词覆盖率：区间内命中的独立词数 / 检索词总数，与向量 score
    同值域 (0,1]。单词 query 命中即 1.0；多词 query 命中越多词的区间
    得分越高，从而有区分度。

    Args:
        path: 待检索的 markdown 文件 Path。
        terms: 检索词列表（逐词 OR 匹配）。

    Returns:
        该文件的命中区间列表（含内容），score 为词覆盖率。
    """
    if not terms:
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    pats = [re.compile(re.escape(t), re.IGNORECASE) for t in terms]
    # 逐行匹配，记录命中行号与该行命中的词索引集合
    hit_lines: list[int] = []
    line_term_sets: list[set[int]] = []
    for i, ln in enumerate(lines, 1):
        hit_idx = {j for j, p in enumerate(pats) if p.search(ln)}
        if hit_idx:
            hit_lines.append(i)
            line_term_sets.append(hit_idx)
    if not hit_lines:
        return []
    n_terms = len(terms)
    out: list[RawResult] = []
    cursor = 0
    for s, e in _merge_ranges([(h, h) for h in hit_lines]):
        # 收集该区间内所有命中行的词索引并集（hit_lines 升序、区间按序覆盖）
        term_union: set[int] = set()
        while cursor < len(hit_lines) and hit_lines[cursor] <= e:
            term_union |= line_term_sets[cursor]
            cursor += 1
        out.append(
            RawResult(
                file_path=path.name,
                start_line=s,
                end_line=e,
                heading="",
                content="\n".join(lines[s - 1 : e]),
                score=len(term_union) / n_terms,
                source="grep",
                resource_id=resource_id or path.stem,
                scope=scope,
                display_name=display_name or path.name,
            )
        )
    dlog(
        "rag.retrieval",
        "_grep_file",
        f"{path.name} grep 命中",
        terms=terms,
        hit_lines_n=len(hit_lines),
        ranges_n=len(out),
    )
    return out


# --------------------------------------------------------------------------- #
# keyword 管道
# --------------------------------------------------------------------------- #
def _resource_names(principal_id: str) -> dict[str, tuple[str, str]]:
    if not principal_id:
        return {}
    repository = KnowledgeRepository(APP_DB_PATH)
    try:
        return {
            item.storage_name: (item.id, item.display_name)
            for item in repository.list(principal_id)
            if item.status == "ready"
        }
    finally:
        repository.close()


def _public_names() -> dict[str, tuple[str, str]]:
    scope = resolve_knowledge_scope("public")
    names: dict[str, tuple[str, str]] = {}
    metadata_dir = scope.documents_dir / ".metadata"
    if not metadata_dir.exists():
        return names
    for metadata in metadata_dir.glob("*.name"):
        storage_name = f"{metadata.stem}.md"
        if (scope.documents_dir / storage_name).exists():
            names[storage_name] = (
                metadata.stem,
                metadata.read_text(encoding="utf-8").strip() or storage_name,
            )
    return names


def _public_ready_files(scope: KnowledgeScope) -> set[str]:
    metadata_dir = scope.documents_dir / ".metadata"
    pending = (
        {f"{path.stem}.md" for path in metadata_dir.glob("*.pending")}
        if metadata_dir.exists()
        else set()
    )
    return {path.name for path in scope.documents_dir.glob("*.md")} - pending


def _keyword_scope(
    search_query: str,
    terms: list[str],
    target: KnowledgeScope,
    names: dict[str, tuple[str, str]],
) -> list[RawResult]:
    if not target.index_path.exists():
        return []
    rows = _parse_index(target.index_path.read_text(encoding="utf-8"))
    candidate_files: list[str] = []
    for row in rows:
        if any(t in kw or kw in t for t in terms for kw in row["keywords"]):
            candidate_files.extend(row["files"])
    candidate_files = list(dict.fromkeys(candidate_files))
    targets = (
        [
            target.documents_dir / f
            for f in candidate_files
            if (target.documents_dir / f).exists()
        ]
        if candidate_files
        else sorted(target.documents_dir.glob("*.md"))
    )
    if target.kind == "public":
        ready_files = _public_ready_files(target)
        targets = [path for path in targets if path.name in ready_files]
    results: list[RawResult] = []
    for path in targets:
        identity = names.get(path.name)
        results.extend(
            _grep_file(
                path,
                terms,
                scope=target.kind,
                resource_id=identity[0] if identity else path.stem,
                display_name=identity[1] if identity else path.name,
            )
        )
    return results


def keyword_retrieve(search_query: str, principal_id: str = "") -> list[RawResult]:
    """Keyword 管道：index.md 关键词匹配 → 候选文件定向 grep 或无候选全目录 grep。

    Args:
        search_query: 主图 Router 提供的检索词。

    Returns:
        原始检索结果列表（可能为空，由 aggregate 判 gap）。
    """
    terms = _extract_terms(search_query)
    dlog("rag.retrieval", "keyword_retrieve", "进入", query=search_query, terms=terms)
    results = _keyword_scope(
        search_query, terms, resolve_knowledge_scope("public"), _public_names()
    )
    if principal_id:
        results.extend(
            _keyword_scope(
                search_query,
                terms,
                resolve_knowledge_scope("personal", principal_id),
                _resource_names(principal_id),
            )
        )
    dlog("rag.retrieval", "keyword_retrieve", "完成", results_n=len(results))
    return results


# --------------------------------------------------------------------------- #
# semantic 管道
# --------------------------------------------------------------------------- #
def _expand_heading_sections(recalled: list[RawResult]) -> list[RawResult]:
    """按 (file_path, heading) 合并相邻 / 重叠行区间（标题节扩展）。

    Args:
        recalled: 向量召回原始 chunk 列表。

    Returns:
        按标题节合并扩展后的结果列表。
    """
    groups: dict[tuple[str, str], list[RawResult]] = {}
    for c in recalled:
        groups.setdefault((c.get("resource_id", c["file_path"]), c["heading"]), []).append(c)

    out: list[RawResult] = []
    for (_, heading), chunks in groups.items():
        ranges = _merge_ranges([(c["start_line"], c["end_line"]) for c in chunks])
        best_score = max(c["score"] for c in chunks)
        content = "\n".join(c["content"] for c in chunks)
        for s, e in ranges:
            out.append(
                RawResult(
                    file_path=chunks[0]["file_path"],
                    start_line=s,
                    end_line=e,
                    heading=heading,
                    content=content,
                    score=best_score,
                    source="vector",
                    resource_id=chunks[0].get("resource_id", chunks[0]["file_path"]),
                    scope=chunks[0].get("scope", "public"),
                    display_name=chunks[0].get(
                        "display_name", chunks[0]["file_path"]
                    ),
                )
            )
    return out


def _compute_confidence(
    results: list[RawResult],
) -> tuple[float, float, float]:
    """计算 top_score / avg_score 分差比。

    Args:
        results: 待评估的结果列表。

    Returns:
        (top_score, avg_score, ratio) 元组；空列表时返回 (0, 0, 1)。
    """
    scores = [r["score"] for r in results]
    if not scores:
        return 0.0, 0.0, 1.0
    top_score = max(scores)
    avg_score = sum(scores) / len(scores)
    ratio = top_score / avg_score if avg_score > 0 else 1.0
    return top_score, avg_score, ratio


_chroma_cols: dict[str, Any] = {}
"""Chroma collection 单例（懒加载，避免每次 retrieve new PersistentClient 触发
Rust backend 初始化竞态——首次 new 时 RustBindingsAPI.bindings 偶发未创建，
stop() ``del self.bindings`` 抛 AttributeError）。"""


def _get_chroma_collection(scope: KnowledgeScope) -> Any:
    """获取（惰性创建并缓存的）Chroma collection 单例。

    单例化后只在首次检索 new 一次 PersistentClient；首次若失败（单例仍 None），
    下次重试时 Rust 扩展已进程级加载，初始化稳定成功并缓存复用——故"刷新后好了"。
    """
    if scope.collection_name not in _chroma_cols:
        import chromadb

        from kernel.embedder import get_embed_fn

        client = chromadb.PersistentClient(path=str(scope.chroma_path))
        _chroma_cols[scope.collection_name] = client.get_or_create_collection(
            scope.collection_name,
            embedding_function=get_embed_fn(),
            metadata={"hnsw:space": "cosine"},
        )
    return _chroma_cols[scope.collection_name]


def _semantic_scope(
    search_query: str,
    scope: KnowledgeScope,
    names: dict[str, tuple[str, str]],
) -> list[RawResult]:
    """Semantic 管道：向量检索 → 标题节扩展 → score 阈值过滤。

    按 ``SCORE_LOW_THRESHOLD`` 过滤召回结果；过滤后为空则由
    ``aggregate_results`` 判 gap。返回值可为空。

    Args:
        search_query: 检索词。

    Returns:
        过滤后的检索结果（score ≥ 阈值），或空列表（gap 场景）。
    """
    col = _get_chroma_collection(scope)
    if col.count() == 0:
        return []
    query_args: dict[str, Any] = {
        "query_texts": [search_query],
        "n_results": VECTOR_N_RESULTS,
    }
    if scope.kind == "personal":
        ready_resource_ids = [resource_id for resource_id, _ in names.values()]
        if not ready_resource_ids:
            return []
        query_args["where"] = {"resource_id": {"$in": ready_resource_ids}}
    else:
        ready_files = sorted(_public_ready_files(scope))
        if not ready_files:
            return []
        query_args["where"] = {"file_path": {"$in": ready_files}}
    res: QueryResult = col.query(**query_args)
    ids = res["ids"]
    metas = res["metadatas"]
    dists = res["distances"]
    docs = res["documents"]
    assert (
        ids is not None and metas is not None and dists is not None and docs is not None
    )
    dlog(
        "rag.retrieval",
        "semantic_retrieve",
        "向量召回",
        query=search_query,
        n_results=VECTOR_N_RESULTS,
    )

    recalled: list[RawResult] = []
    for i in range(len(ids[0])):
        meta = metas[0][i]
        dist = dists[0][i]
        identity = names.get(str(meta["file_path"]))
        recalled.append(
            RawResult(
                file_path=identity[1] if identity else str(meta["file_path"]),
                start_line=int(meta["start_line"]),  # type: ignore[arg-type]
                end_line=int(meta["end_line"]),  # type: ignore[arg-type]
                heading=str(meta["heading"]),
                content=docs[0][i],
                score=1.0 / (1.0 + dist),
                source="vector",
                resource_id=str(
                    meta.get("resource_id")
                    or (identity[0] if identity else Path(str(meta["file_path"])).stem)
                ),
                scope=scope.kind,
                display_name=identity[1] if identity else str(meta["file_path"]),
            )
        )

    expanded = _expand_heading_sections(recalled)
    top_score, avg_score, ratio = _compute_confidence(expanded)
    dlog(
        "rag.retrieval",
        "semantic_retrieve",
        "召回明细",
        recalled_n=len(recalled),
        expanded_n=len(expanded),
        top_score=round(top_score, 3),
        avg_score=round(avg_score, 3),
        ratio=round(ratio, 3),
        top3=[(r["file_path"], round(r["score"], 3)) for r in recalled[:3]],
    )
    # 按 score 阈值过滤；gap 由 aggregate_results（raw 为空）判定。
    # TODO(rag-agent): 未来用 LLM 智能汇总节点替代纯 score 阈值——由 LLM
    # 判断哪些召回片段真正回答了 query、是否存在知识缺口（gap_topic），
    # 而非依赖 SCORE_LOW_THRESHOLD 硬阈值（相关文档多且 score 接近时
    # 易误判，原 confidence ratio 规则已因此废弃）。
    filtered = [r for r in expanded if r["score"] >= SCORE_LOW_THRESHOLD]
    dlog(
        "rag.retrieval",
        "semantic_retrieve",
        "完成",
        filtered_n=len(filtered),
    )
    return filtered


def semantic_retrieve(search_query: str, principal_id: str = "") -> list[RawResult]:
    """分别检索公共与当前 principal 的个人 collection 后合并排序。"""
    results = _semantic_scope(
        search_query, resolve_knowledge_scope("public"), _public_names()
    )
    if principal_id:
        names = _resource_names(principal_id)
        results.extend(
            _semantic_scope(
                search_query,
                resolve_knowledge_scope("personal", principal_id),
                names,
            )
        )
    return sorted(results, key=lambda item: item["score"], reverse=True)


# --------------------------------------------------------------------------- #
# 外部接口
# --------------------------------------------------------------------------- #
def retrieve_pipeline(
    search_query: str, search_type: str, principal_id: str = ""
) -> list[RawResult]:
    """按 search_type 分派并执行对应管道。

    两种 ``search_type`` 对 ``search_query`` 的要求不同：

    - ``"semantic"``（语义检索）：
      传入**完整的自然语言问句**或上下文丰富的描述句。
      底层依赖 Chroma 向量余弦相似度排序，完整句子能产生更准确的
      语义向量，从而提升召回质量。例如：
        ✓ ``"StateGraph 中的 add_messages 是如何累加的？"``
        ✗ ``"add_messages 累加"``（过于简短，向量区分度低）

    - ``"keyword"``（关键词检索）：
      传入**空白符分隔的关键词列表**（由主图 agent 生成）。
      底层按空白符切分后去 ``index.md`` 做关键词匹配 + 定向 Grep。
      不需要完整句子，但多个关键词之间必须用空格隔开。
      例如：
        ✓ ``"StateGraph add_messages 消息累加"``
        ✓ ``"StateGraph 条件边 send"``

    Args:
        search_query: 检索词（形式要求见上方说明）。
        search_type: ``"semantic"`` 或 ``"keyword"``。

    Returns:
        管道产出的原始结果列表（未经 aggregate 处理）。
    """
    dlog(
        "rag.retrieval",
        "retrieve_pipeline",
        "分派",
        search_type=search_type,
        query=search_query,
    )
    if search_type == "keyword":
        return keyword_retrieve(search_query, principal_id)
    return semantic_retrieve(search_query, principal_id)


def aggregate_results(
    raw_results: list[RawResult], search_query: str
) -> tuple[list[Citation], str | None]:
    """纯规则汇总：有效命中 > 0 时排序输出，否则判 gap。

    Args:
        raw_results: 管道产出的原始结果。
        search_query: 原检索词（gap 时回填为 gap_topic）。

    Returns:
        (citations_output, gap_topic) 二元组。
    """
    if not raw_results:
        dlog(
            "rag.retrieval",
            "aggregate_results",
            "raw 为空 → gap",
            gap_topic=search_query,
        )
        return [], search_query
    ranked = sorted(raw_results, key=lambda r: r["score"], reverse=True)
    citations = [
        Citation(
            file_path=r["file_path"],
            start_line=r["start_line"],
            end_line=r["end_line"],
            content=r["content"],
            score=r["score"],
            resource_id=r.get("resource_id", r["file_path"]),
            scope=r.get("scope", "public"),
            display_name=r.get("display_name", r["file_path"]),
        )
        for r in ranked
    ]
    dlog(
        "rag.retrieval",
        "aggregate_results",
        "完成",
        raw_n=len(raw_results),
        citations_n=len(citations),
        top_score=round(citations[0]["score"], 3) if citations else 0.0,
    )
    return citations, None
