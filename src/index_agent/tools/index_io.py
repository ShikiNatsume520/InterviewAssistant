"""index.md 索引文件的读写与渲染工具。

data/index.md 是 LLM 维护的文件级语义索引（markdown 表格）：
每行一条索引 = 关键词 | LLM 摘要 | 文件引用（[文件名](data/markdown/文件名)，无行号）。
"""

from __future__ import annotations

from pathlib import Path

from index_agent.state import IndexRow

HEADER_LINES = [
    "# 知识库索引",
    "",
    "> 由 Index Agent 维护，请勿手动编辑。每行一条索引：关键词 | LLM 摘要 | 文件引用。",
    "",
    "| 关键词 | 摘要 | 引用 |",
    "|--------|------|------|",
]
"""index.md 的固定表头行。"""

TABLE_START_LINE = len(HEADER_LINES)
"""表格数据行的起始行号（0-based，即表头之后的第 1 行）。"""


def render_index_md(rows: list[IndexRow]) -> str:
    """把索引行列表渲染成 data/index.md 全文（含表头）。

    Args:
        rows: IndexRow 列表。

    Returns:
        完整的 index.md 文本（UTF-8，末尾换行）。
    """
    lines = list(HEADER_LINES)
    for r in rows:
        kw = ", ".join(r.keywords)
        cites = " ".join(f"[{f}](data/markdown/{f})" for f in r.files)
        lines.append(f"| {kw} | {r.summary} | {cites} |")
    return "\n".join(lines) + "\n"


def parse_index_md(text: str) -> list[IndexRow]:
    """从 index.md 文本解析出索引行列表（跳过表头与非数据行）。

    Args:
        text: index.md 全文。

    Returns:
        IndexRow 列表（按文件中出现顺序）。
    """
    rows: list[IndexRow] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        # 跳过表头行（关键词/摘要/引用）
        if cells[0] == "关键词" or cells[0] == "--------":
            continue
        keywords = [k.strip() for k in cells[0].split(",") if k.strip()]
        summary = cells[1]
        # 从引用单元提取文件名（[文件名](data/markdown/文件名) → 文件名）
        import re

        files = re.findall(r"\[([^\]]+)\]\(data/markdown/([^\]]+)\)", cells[2])
        file_names = [f[1] for f in files]
        rows.append(IndexRow(keywords=keywords, summary=summary, files=file_names))
    return rows


def load_existing_index(path: str | Path) -> list[IndexRow]:
    """读取现有 index.md；不存在则返回空列表（首次构建）。

    Args:
        path: index.md 文件路径。

    Returns:
        现有索引行列表；文件不存在时为 []。
    """
    p = Path(path)
    if not p.exists():
        return []
    return parse_index_md(p.read_text(encoding="utf-8"))


def write_index(path: str | Path, rows: list[IndexRow]) -> None:
    """把索引行渲染并覆盖写回 index.md。

    Args:
        path: index.md 文件路径。
        rows: 更新后的完整索引行列表。
    """
    Path(path).write_text(render_index_md(rows), encoding="utf-8")
