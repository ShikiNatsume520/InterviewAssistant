"""Markdown 行级切片工具。

按标题结构切分 markdown；超长块按字符二次切并保留 overlap，防止关键信息被一分为二。
切片 metadata 严格保留 start_line/end_line，满足行级引用硬约束。

实现说明：
- 行号基于 1-based 闭区间 [start_line, end_line]。
- 代码围栏（``` / ~~~）内的 # 行不当标题处理。
- 超长二次切时，overlap 体现在行号区间重叠（相邻 chunk 末尾/开头行重叠）。
"""

from __future__ import annotations

import bisect
import re
from pathlib import Path

from index_agent.state import Chunk

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
"""匹配 markdown 标题行（# ~ ###### 开头）。"""

FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
"""匹配代码围栏起始行（``` 或 ~~~），用于切换 in_fence 状态。"""

MAX_CHARS = 500
"""单 chunk 最大字符数，超过则二次切分。"""

OVERLAP = 50
"""二次切分时相邻 chunk 的字符级重叠量，防止关键信息被切断。"""


def _build_line_offsets(lines: list[str]) -> list[int]:
    """计算每行（0-based 索引）在 ``"\\n".join(lines)`` 全文中的起始字符偏移。

    Args:
        lines: 按行切分后的文本列表（不含换行符）。

    Returns:
        长度等于 len(lines) 的偏移列表，offsets[i] 是第 i 行的起始 char 偏移。
    """
    offsets: list[int] = []
    o = 0
    for line in lines:
        offsets.append(o)
        o += len(line) + 1  # +1 for "\n"
    return offsets


def _offset_to_line(offsets: list[int], off: int) -> int:
    """字符偏移转 1-based 行号（clamp 到 [1, len(offsets)]）。

    Args:
        offsets: _build_line_offsets 的输出。
        off: 全文中的字符偏移。

    Returns:
        该偏移所在的 1-based 行号。
    """
    n = len(offsets)
    return max(1, min(n, bisect.bisect_right(offsets, off)))


def _make_chunk(
    file_path: str, start_line: int, end_line: int, heading: str, content: str
) -> Chunk:
    """构造一个 Chunk（TypedDict）。

    Args:
        file_path: 文件名（不含目录）。
        start_line: 起始行号（1-based）。
        end_line: 结束行号（1-based）。
        heading: 所属标题文本。
        content: 切片纯文本。

    Returns:
        构造好的 Chunk。
    """
    return {
        "file_path": file_path,
        "start_line": start_line,
        "end_line": end_line,
        "heading": heading,
        "content": content,
    }


def _split_by_headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """按标题找块边界，返回每个块的 (start_line, end_line, heading)（1-based 闭区间）。

    代码围栏内的 # 不当标题。首个标题之前的内容（若有）归入一个 heading 为空的前置块。

    Args:
        lines: 按行切分的文本列表。

    Returns:
        块边界列表，每项为 (起始行, 结束行, 标题文本)。
    """
    bounds: list[tuple[int, int, str]] = []
    cur_start: int | None = None
    cur_heading = ""
    in_fence = False
    for i, line in enumerate(lines):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            m = HEADING_RE.match(line)
            if m:
                if cur_start is not None:
                    bounds.append((cur_start, i, cur_heading))
                cur_start = i + 1
                cur_heading = m.group(2).strip()
    if cur_start is not None:
        bounds.append((cur_start, len(lines), cur_heading))
    return bounds


def chunk_markdown(text: str, file_path: str) -> list[Chunk]:
    """切分 markdown 文本为带行号的 chunk 列表。

    Args:
        text: markdown 原文（CRLF/LF 均会被归一化为 LF）。
        file_path: 文件名（不含目录），写入每个 chunk 的 file_path 字段。

    Returns:
        Chunk 列表，每项含 file_path/start_line/end_line/heading/content。
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    offsets = _build_line_offsets(lines)
    full = "\n".join(lines)

    chunks: list[Chunk] = []
    for start_line, end_line, heading in _split_by_headings(lines):
        s_off = offsets[start_line - 1]
        e_off = offsets[end_line - 1] + len(lines[end_line - 1])
        if e_off - s_off <= MAX_CHARS:
            chunks.append(
                _make_chunk(file_path, start_line, end_line, heading, full[s_off:e_off])
            )
            continue
        # 超长：按字符级二次切，带 overlap
        pos = s_off
        while pos < e_off:
            cend = min(pos + MAX_CHARS, e_off)
            chunks.append(
                _make_chunk(
                    file_path,
                    _offset_to_line(offsets, pos),
                    _offset_to_line(offsets, cend - 1),
                    heading,
                    full[pos:cend],
                )
            )
            if cend >= e_off:
                break
            next_pos = cend - OVERLAP
            if next_pos <= pos:  # 保险：保证前进，防死循环
                next_pos = pos + 1
            pos = next_pos
    return chunks


def read_and_chunk(file_path: Path) -> list[Chunk]:
    """读取单个 markdown 文件并切片。

    Args:
        file_path: markdown 文件的 Path 对象（仅取 .name 作为 chunk 的 file_path）。

    Returns:
        该文件的 Chunk 列表。
    """
    text = file_path.read_text(encoding="utf-8")
    return chunk_markdown(text, file_path.name)
