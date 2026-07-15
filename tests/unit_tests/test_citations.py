from server.citations import extract_used_citations, parse_reference_tail


def test_parse_strict_reference_tail_with_chinese_and_spaced_filename() -> None:
    parsed = parse_reference_tail(
        "RAG 会先检索再生成[1]。\n\n"
        "## 参考资料\n"
        "[1] 中文 file name.md L12-28"
    )

    assert parsed is not None
    assert parsed[0] == "RAG 会先检索再生成[1]。"
    assert parsed[1][0].file_path == "中文 file name.md"


def test_parser_rejects_non_strict_or_non_tail_blocks() -> None:
    invalid_samples = (
        "正文\n\n## 参考资料\n- [1] a.md L1-2",
        "正文\n\n## 参考资料\n[2] a.md L1-2",
        "正文\n\n## 参考资料\n[1] a.md L2-1",
        "正文\n\n## 参考资料\n[1] a.md L1–2",
        "正文\n\n## 参考资料\n[1] a.md L1-2\n后续内容",
    )

    assert all(parse_reference_tail(sample) is None for sample in invalid_samples)


def test_extract_used_citations_keeps_only_valid_declared_subset() -> None:
    candidates = [
        {
            "file_path": "a.md",
            "start_line": 1,
            "end_line": 2,
            "content": "A",
            "score": 0.9,
        },
        {
            "file_path": "b.md",
            "start_line": 3,
            "end_line": 4,
            "content": "B",
            "score": 0.8,
        },
    ]

    extracted = extract_used_citations(
        "只使用第二份资料[1]。\n\n## 参考资料\n[1] b.md L3-4",
        candidates,
    )

    assert extracted == ("只使用第二份资料[1]。", [candidates[1]])


def test_extract_used_citations_rejects_unknown_reference() -> None:
    assert (
        extract_used_citations(
            "正文[1]。\n\n## 参考资料\n[1] unknown.md L1-2", []
        )
        is None
    )
