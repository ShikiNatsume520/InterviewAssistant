"""Phase 1 LLM 索引探针：验证 DeepSeek 端点结构化输出 + 索引合并/新建逻辑。

关键技术风险：
- DeepSeek OpenAI 兼容端点是否支持 ``with_structured_output``（function-calling 式）。
- LLM 能否看「现有索引 + 新文件 chunks」后正确判断 合并 / 新建，输出合法 IndexUpdate。
- 后端用 LLM 输出的 files 字段拼接 ``[文件名](data/markdown/文件名)`` 引用（文件级，无行号）。

环境变量（.env）：
- DEEPSEEK_API_URL  -> base_url
- DEEPSEEK_API_KEY  -> api_key
- DEEPSEEK_MODEL    -> model

运行：
    .venv/Scripts/python.exe prototypes/phase1_llm_index_probe.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from phase1_chunk_probe import chunk_markdown
from pydantic import BaseModel, Field

load_dotenv()

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "markdown"
BASE_URL = os.environ["DEEPSEEK_API_URL"]
API_KEY = os.environ["DEEPSEEK_API_KEY"]
MODEL = os.environ["DEEPSEEK_MODEL"]
SUMMARY_MAX = 100


class IndexRow(BaseModel):
    """单条索引：关键词 + LLM 摘要 + 文件名列表（后端拼引用）。"""

    keywords: list[str] = Field(description="该索引项的核心关键词")
    summary: str = Field(description=f"该索引项的摘要，不超过 {SUMMARY_MAX} 字符")
    files: list[str] = Field(description="该索引项指向的 markdown 文件名列表（不含路径）")


class IndexUpdate(BaseModel):
    """LLM 输出：更新后的完整索引表。"""

    rows: list[IndexRow] = Field(description="更新后的完整索引表（含未变动行原样保留）")


INDEX_PROMPT = """你是知识库索引维护 Agent。你的任务：根据【新入库文件内容】更新【现有索引】，输出更新后的完整索引表。

维护规则：
1. 对新文件的每个语义主题，判断现有索引中是否存在相似或可合并的项：
   - 若有相似项：合并——完善其摘要，并把该新文件名加入该项的 files（去重，保留已有文件）。
   - 若无相似项：新建一条索引行。
2. 与新文件无关的现有索引行，原样保留。
3. 一条索引可指向多个文件。
4. 摘要精炼，不超过 100 字符。
5. files 只写文件名（不含路径），如 langgraph_state.md。
6. 你必须调用 `IndexUpdate` 工具来输出结果，不要直接返回文本。

【现有索引】
{existing_index}

【新入库文件片段】
{new_chunks}

请输出更新后的完整索引表。"""


def _format_existing(rows: list[IndexRow]) -> str:
    """把现有索引行渲染成给 LLM 看的文本。"""
    if not rows:
        return "（空，首次构建）"
    lines = []
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. 关键词: {r.keywords} | 摘要: {r.summary} | 文件: {r.files}")
    return "\n".join(lines)


def _format_chunks(chunks: list[dict[str, Any]]) -> str:
    """把新文件 chunks 渲染成给 LLM 看的文本（带 heading + 内容片段）。"""
    parts = []
    for c in chunks:
        parts.append(
            f"--- 文件: {c['file_path']} | 小节: {c['heading']} (L{c['start_line']}-L{c['end_line']}) ---\n"
            f"{c['content']}"
        )
    return "\n\n".join(parts)


def _render_index_md(rows: list[IndexRow]) -> str:
    """把 IndexUpdate 渲染成 data/index.md 表格（文件级引用，后端拼）。"""
    lines = [
        "# 知识库索引",
        "",
        "> 由 Index Agent 维护，请勿手动编辑。每行一条索引：关键词 | LLM 摘要 | 文件引用。",
        "",
        "| 关键词 | 摘要 | 引用 |",
        "|--------|------|------|",
    ]
    for r in rows:
        kw = ", ".join(r.keywords)
        cites = " ".join(f"[{f}](data/markdown/{f})" for f in r.files)
        lines.append(f"| {kw} | {r.summary} | {cites} |")
    return "\n".join(lines) + "\n"


async def main() -> None:
    """首次构建：现有索引为空 + 两篇新文件 -> LLM 输出索引 -> 渲染 index.md。"""
    # 1) 切两篇样例
    chunks: list[dict[str, Any]] = []
    for fp in sorted(DATA_DIR.glob("*.md")):
        chunks.extend(chunk_markdown(fp.read_text(encoding="utf-8"), fp.name))

    # 2) 构造 LLM
    #    DeepSeek thinking 模型不能强制 tool_choice（with_structured_output 会塞
    #    tool_choice=required 触发 400）。改用 bind_tools 提供工具，走默认 auto 让
    #    LLM 自行决定调用——思考模型在 prompt 明确要求时会主动调用工具。
    llm = ChatOpenAI(
        base_url=BASE_URL,
        api_key=API_KEY,
        model=MODEL,
        temperature=0.0,
        max_tokens=1500,
    ).bind_tools([IndexUpdate])

    async def call_llm(prompt: str) -> IndexUpdate:
        """调 LLM，优先取 tool_calls；未调用工具则兜底从 content 解析 JSON。"""
        msg = await llm.ainvoke(prompt)
        if msg.tool_calls:
            args = msg.tool_calls[0]["args"]
            return IndexUpdate.model_validate(args)
        # 兜底：未调用工具，尝试从文本里抽 JSON
        text = msg.content or ""
        print(f"[llm_index_probe] WARN: 未调用工具，兜底解析文本 ({len(text)} 字符)")
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise RuntimeError(f"LLM 未调用工具且无 JSON 文本: {text[:120]}")
        import json as _json

        return IndexUpdate.model_validate(_json.loads(text[start : end + 1]))

    # 3) 首次构建：现有索引空
    prompt = INDEX_PROMPT.format(
        existing_index=_format_existing([]), new_chunks=_format_chunks(chunks)
    )
    print("[llm_index_probe] 首次构建，调用 LLM ...")
    result = await call_llm(prompt)
    print(f"[llm_index_probe] LLM 返回 {len(result.rows)} 条索引行")

    for i, r in enumerate(result.rows, 1):
        print(f"  {i}. kw={r.keywords}")
        print(f"     summary({len(r.summary)}字)={r.summary}")
        print(f"     files={r.files}")

    # 4) 渲染 index.md
    md = _render_index_md(result.rows)
    print("\n=== data/index.md 预览 ===")
    print(md)

    # 5) 二次调用：把首次结果当现有索引，再喂同样文件，验证幂等/合并
    prompt2 = INDEX_PROMPT.format(
        existing_index=_format_existing(result.rows), new_chunks=_format_chunks(chunks)
    )
    print("\n[llm_index_probe] 二次构建（验证幂等/合并），调用 LLM ...")
    result2 = await call_llm(prompt2)
    print(f"[llm_index_probe] 二次返回 {len(result2.rows)} 条索引行")
    for i, r in enumerate(result2.rows, 1):
        print(f"  {i}. kw={r.keywords} | files={r.files}")

    print("\n[llm_index_probe] OK: 结构化输出 + 索引合并/新建逻辑验证通过")


if __name__ == "__main__":
    asyncio.run(main())
