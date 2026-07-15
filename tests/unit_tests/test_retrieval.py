"""retrieval 纯规则逻辑的精简单元测试。

覆盖：
- ``_grep_file`` 词覆盖率置信度（单词 / 多词 / 无命中）。
- ``aggregate_results`` 保留 score 且按降序排序。
- ``Citation`` 字段断言。
"""

from __future__ import annotations

from pathlib import Path

from agents.rag.state import RawResult
from agents.rag.tools.retrieval import _grep_file, aggregate_results


def _write_tmp_md(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_grep_single_term_hit_score_is_one(tmp_path: Path) -> None:
    """单词 query 命中 → score = 1.0。"""
    p = _write_tmp_md(
        tmp_path,
        "a.md",
        "StateGraph 是核心类\n其他内容\nStateGraph 又出现一次\n",
    )
    results = _grep_file(p, ["StateGraph"])
    assert len(results) >= 1
    assert all(r["score"] == 1.0 for r in results)
    assert all(r["source"] == "grep" for r in results)
    assert all(
        r["start_line"] >= 1 and r["end_line"] >= r["start_line"] for r in results
    )


def test_grep_multi_term_coverage(tmp_path: Path) -> None:
    """3 词 query，某区间命中 2 词 → score = 2/3。"""
    content = "StateGraph 条件边\n纯 StateGraph 的行\n无关行\n"
    p = _write_tmp_md(tmp_path, "a.md", content)
    results = _grep_file(p, ["StateGraph", "条件边", "send"])
    # 检索词 3 个，本文件命中了 StateGraph 与 条件边 两个 → 覆盖率 2/3
    assert len(results) >= 1
    top = max(r["score"] for r in results)
    assert top == 2 / 3
    # 全部命中 score 不超过 1.0、不低于 0
    assert all(0.0 < r["score"] <= 1.0 for r in results)


def test_grep_no_hit_returns_empty(tmp_path: Path) -> None:
    p = _write_tmp_md(tmp_path, "a.md", "完全不相关的文本\n第二行\n")
    assert _grep_file(p, ["StateGraph", "条件边"]) == []


def test_grep_empty_terms_returns_empty(tmp_path: Path) -> None:
    p = _write_tmp_md(tmp_path, "a.md", "任何内容\n")
    assert _grep_file(p, []) == []


def test_aggregate_preserves_score_and_sorts_desc() -> None:
    """aggregate_results 保留 score 字段并按 score 降序。"""
    raw = [
        RawResult(
            file_path="a.md",
            start_line=10,
            end_line=20,
            heading="",
            content="低分",
            score=0.3,
            source="vector",
        ),
        RawResult(
            file_path="b.md",
            start_line=5,
            end_line=8,
            heading="",
            content="高分",
            score=0.9,
            source="grep",
        ),
        RawResult(
            file_path="c.md",
            start_line=1,
            end_line=2,
            heading="",
            content="中分",
            score=0.6,
            source="vector",
        ),
    ]
    citations, gap = aggregate_results(raw, "dummy")
    assert gap is None
    assert len(citations) == 3
    # 降序
    assert citations[0]["score"] == 0.9
    assert citations[1]["score"] == 0.6
    assert citations[2]["score"] == 0.3
    # Citation 字段齐全
    for c in citations:
        assert set(c.keys()) == {
            "file_path",
            "start_line",
            "end_line",
            "content",
            "score",
        }


def test_aggregate_empty_returns_gap() -> None:
    citations, gap = aggregate_results([], "缺口主题")
    assert citations == []
    assert gap == "缺口主题"
