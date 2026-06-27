"""Phase 1 切片探针：验证 Markdown 行级切片的行号准确性。

关键技术风险：切片后 chunk 的 ``start_line``/``end_line`` 能准确对回原文行号。

切片规则（与 Phase 1 正式实现一致）：
- 按 Markdown 标题切（遇 ``#`` 标题行开始新块）；代码围栏内的 ``#`` 不当标题。
- 单块字符数 <= 500 时整块作为一个 chunk；超过则按字符级二次切，带 50 字符 overlap
  （overlap 体现在行号区间重叠，防止关键信息被一分为二）。
- 每 chunk metadata：file_path / start_line / end_line / heading / content。

本探针不依赖外部包（纯文本处理），位于 prototypes/，不污染 src/。
运行：
    .venv/Scripts/python.exe prototypes/phase1_chunk_probe.py
"""

from __future__ import annotations

import bisect
import re
from pathlib import Path
from typing import Any

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
MAX_CHARS = 500
OVERLAP = 50

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "markdown"


def _build_line_offsets(lines: list[str]) -> list[int]:
    """每行（0-based）在 ``"\\n".join(lines)`` 后全文中的起始 char 偏移。"""
    offsets: list[int] = []
    o = 0
    for line in lines:
        offsets.append(o)
        o += len(line) + 1  # +1 for "\n"
    return offsets


def _offset_to_line(offsets: list[int], off: int) -> int:
    """char 偏移 → 1-based 行号（clamp 到 [1, n]）。"""
    n = len(offsets)
    return max(1, min(n, bisect.bisect_right(offsets, off)))


def _make_chunk(
    file_path: str, start_line: int, end_line: int, heading: str, content: str
) -> dict[str, Any]:
    """构造一个 chunk dict。"""
    return {
        "file_path": file_path,
        "start_line": start_line,
        "end_line": end_line,
        "heading": heading,
        "content": content,
    }


def chunk_markdown(text: str, file_path: str) -> list[dict[str, Any]]:
    """切分 markdown 文本，返回带行号的 chunk 列表。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    n = len(lines)
    offsets = _build_line_offsets(lines)
    full = "\n".join(lines)

    def off2line(off: int) -> int:
        return _offset_to_line(offsets, off)

    # 1) 按标题找块边界（行级，1-based 闭区间 [start, end]）
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
                    # 上一块 end = 当前标题行（0-based i = 1-based 上一块末行）
                    bounds.append((cur_start, i, cur_heading))
                cur_start = i + 1
                cur_heading = m.group(2).strip()
    if cur_start is not None:
        bounds.append((cur_start, n, cur_heading))

    # 2) 块内按长度切（字符级 + overlap）
    chunks: list[dict[str, Any]] = []
    for start_line, end_line, heading in bounds:
        s_off = offsets[start_line - 1]
        e_off = offsets[end_line - 1] + len(lines[end_line - 1])
        if e_off - s_off <= MAX_CHARS:
            chunks.append(_make_chunk(file_path, start_line, end_line, heading, full[s_off:e_off]))
            continue
        pos = s_off
        while pos < e_off:
            cend = min(pos + MAX_CHARS, e_off)
            chunks.append(
                _make_chunk(
                    file_path,
                    off2line(pos),
                    off2line(cend - 1),
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


def verify_chunks(chunks: list[dict[str, Any]], text: str) -> None:
    """断言每个 chunk 的 content 落在其 start_line~end_line 对应的原文范围内。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    offsets = _build_line_offsets(lines)
    full = "\n".join(lines)
    for c in chunks:
        s, e = c["start_line"], c["end_line"]
        block_text = full[offsets[s - 1] : offsets[e - 1] + len(lines[e - 1])]
        assert c["content"] in block_text, (
            f"行号不准: {c['file_path']} L{s}-{e}\ncontent 不在原文区间内"
        )
        # 首字符应属于 start_line
        first_off = full.index(c["content"][0], offsets[s - 1])
        assert _offset_to_line(offsets, first_off) == s, (
            f"首字符行号不准: {c['file_path']} 期望 L{s}"
        )


def _preview(s: str, width: int = 60) -> str:
    """单行预览（替换换行为 `\\n` 字面，避免 Windows GBK 控制台编码问题）。"""
    return s.replace("\r", "").replace("\n", "\\n")[:width]


async def main() -> None:
    """跑两篇样例，打印 chunk 摘要并断言行号准确。"""
    files = sorted(DATA_DIR.glob("*.md"))
    assert files, f"无样例 md: {DATA_DIR}"
    for fp in files:
        text = fp.read_text(encoding="utf-8")
        chunks = chunk_markdown(text, fp.name)
        verify_chunks(chunks, text)
        print(f"\n=== {fp.name}（{len(text)} 字符 → {len(chunks)} chunks）===")
        for c in chunks:
            print(
                f"  L{c['start_line']:>3}-{c['end_line']:<3} "
                f"[{c['heading'][:20]}] "
                f"({len(c['content'])}字) "
                f"{_preview(c['content'])}"
            )
    print("\n[chunk_probe] OK: 所有 chunk 行号断言通过")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
