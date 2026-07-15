"""把主 Agent 声明的尾部参考资料转换为结构化产品数据。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from agents.rag.state import Citation

_HEADING = "## 参考资料"
_REFERENCE_LINE = re.compile(
    r"^\[(?P<number>[1-9]\d*)\]\s+(?P<file>.+?)\s+"
    r"L(?P<start>[1-9]\d*)-(?P<end>[1-9]\d*)$"
)


@dataclass(frozen=True)
class DeclaredReference:
    """回答尾部声明的一条引用。"""

    number: int
    file_path: str
    start_line: int
    end_line: int


def parse_reference_tail(text: str) -> tuple[str, list[DeclaredReference]] | None:
    """严格解析回答末尾的参考资料块，失败时不修改原文。"""
    marker = f"\n{_HEADING}\n"
    split_at = text.rfind(marker)
    if split_at < 0:
        return None

    body = text[:split_at].rstrip()
    lines = text[split_at + len(marker) :].splitlines()
    if not body or not lines or any(not line for line in lines):
        return None

    references: list[DeclaredReference] = []
    for expected_number, line in enumerate(lines, 1):
        match = _REFERENCE_LINE.fullmatch(line)
        if match is None or int(match["number"]) != expected_number:
            return None
        start_line = int(match["start"])
        end_line = int(match["end"])
        if start_line > end_line:
            return None
        references.append(
            DeclaredReference(
                number=expected_number,
                file_path=match["file"],
                start_line=start_line,
                end_line=end_line,
            )
        )
    return body, references


def extract_used_citations(
    text: str, candidates: Sequence[Citation]
) -> tuple[str, list[Citation]] | None:
    """解析并校验引用；所有声明均须精确匹配本轮 RAG 候选。"""
    parsed = parse_reference_tail(text)
    if parsed is None:
        return None

    body, references = parsed
    by_location = {
        (item["file_path"], item["start_line"], item["end_line"]): item
        for item in candidates
    }
    used: list[Citation] = []
    for reference in references:
        candidate = by_location.get(
            (reference.file_path, reference.start_line, reference.end_line)
        )
        if candidate is None:
            return None
        used.append(candidate)
    return body, used
