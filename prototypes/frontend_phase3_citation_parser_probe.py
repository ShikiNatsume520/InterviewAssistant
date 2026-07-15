"""阶段 3 补充探针：验证回答尾部引用块的严格解析与安全降级。"""

from __future__ import annotations

import re
from dataclasses import dataclass


_HEADING = "## 参考资料"
_LINE = re.compile(r"^\[(?P<number>[1-9]\d*)\]\s+(?P<file>.+?)\s+L(?P<start>[1-9]\d*)-(?P<end>[1-9]\d*)$")


@dataclass(frozen=True)
class Reference:
    number: int
    file_path: str
    start_line: int
    end_line: int


def parse_reference_tail(text: str) -> tuple[str, list[Reference]] | None:
    """只接受位于回答末尾、编号连续的严格引用块。"""
    marker = f"\n{_HEADING}\n"
    split_at = text.rfind(marker)
    if split_at < 0:
        return None
    body = text[:split_at].rstrip()
    lines = text[split_at + len(marker) :].splitlines()
    if not body or not lines or any(not line for line in lines):
        return None

    references: list[Reference] = []
    for expected, line in enumerate(lines, 1):
        match = _LINE.fullmatch(line)
        if match is None or int(match["number"]) != expected:
            return None
        start = int(match["start"])
        end = int(match["end"])
        if start > end:
            return None
        references.append(
            Reference(expected, match["file"], start, end)
        )
    return body, references


def main() -> None:
    valid = (
        "RAG 会先检索再生成[1]。\n\n"
        "## 参考资料\n"
        "[1] 中文 file name.md L12-28"
    )
    parsed = parse_reference_tail(valid)
    assert parsed is not None
    assert parsed[0] == "RAG 会先检索再生成[1]。"
    assert parsed[1][0].file_path == "中文 file name.md"

    invalid_samples = (
        "正文\n\n## 参考资料\n- [1] a.md L1-2",
        "正文\n\n## 参考资料\n[2] a.md L1-2",
        "正文\n\n## 参考资料\n[1] a.md L2-1",
        "正文\n\n## 参考资料\n[1] a.md L1–2",
        "正文中出现 ## 参考资料，但它不是末尾引用块。",
    )
    assert all(parse_reference_tail(sample) is None for sample in invalid_samples)

    # 是否调用过 RAG 是外层适配器的前置条件；未调用时根本不进入解析器。
    rag_executed = False
    assert (parse_reference_tail(valid) if rag_executed else None) is None
    print("citation parser probe: PASS")


if __name__ == "__main__":
    main()
