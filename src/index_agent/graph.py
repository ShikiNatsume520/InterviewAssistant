"""Index Agent 的图定义（五节点线性后台 agent）。

职责：对显式传入的 target_files（新入库 markdown）做行级切片 → 向量化灌入 Chroma →
LLM 维护 data/index.md。不进主图流程、不进 AgentRegistry，仅由 SDK 测试 / 后续
非对话导入接口唤醒。

节点链：
    scan_node → chunk_node → embed_node → llm_index_node → write_index_node
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from index_agent.state import IndexAgentState, IndexRow
from index_agent.tools.chunking import read_and_chunk
from index_agent.tools.index_io import load_existing_index, write_index
from index_agent.tools.llm_index import build_llm, update_index_via_llm
from index_agent.tools.vectorstore import make_persistent_client, upsert_chunks

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
"""项目根目录（src/ 的上两级）。"""

MARKDOWN_DIR = PROJECT_ROOT / "data" / "markdown"
"""本地 markdown 知识库目录。"""

INDEX_MD_PATH = PROJECT_ROOT / "data" / "index.md"
"""LLM 维护的索引文件路径。"""

CHROMA_PATH = PROJECT_ROOT / "data" / "chroma"
"""Chroma 向量库持久化目录。"""

_llm = build_llm()
"""模块级 LLM 实例（避免每次节点调用重建，复用连接）。"""


def _resolve_target_files(target_files: list[str]) -> list[Path]:
    """把文件名列表解析为存在的 markdown 文件 Path 列表（跳过不存在者）。

    Args:
        target_files: 文件名列表（不含目录前缀）。

    Returns:
        在 MARKDOWN_DIR 下真实存在的文件 Path 列表。
    """
    out: list[Path] = []
    for name in target_files:
        fp = MARKDOWN_DIR / name
        if fp.exists():
            out.append(fp)
    return out


def scan_node(state: IndexAgentState) -> dict[str, Any]:
    """扫描节点：读 target_files 校验存在性 + 读现有 index.md。

    不扫描全目录——只认显式传入的 target_files。

    Args:
        state: 图状态，需含 target_files。

    Returns:
        更新 existing_rows（现有索引行，无 index.md 时为空）。
    """
    existing = load_existing_index(INDEX_MD_PATH)
    return {"existing_rows": existing}


def chunk_node(state: IndexAgentState) -> dict[str, Any]:
    """切片节点：对 target_files 做行级切片（metadata 带行号）。

    Args:
        state: 图状态，需含 target_files。

    Returns:
        更新 chunks（Chunk 列表）。
    """
    target_files = state.get("target_files", [])
    paths = _resolve_target_files(target_files)
    chunks = []
    for fp in paths:
        chunks.extend(read_and_chunk(fp))
    return {"chunks": chunks}


def embed_node(state: IndexAgentState) -> dict[str, Any]:
    """灌库节点：把切片向量化并 upsert 进 Chroma（按 chunk_id 去重）。

    Args:
        state: 图状态，需含 chunks。

    Returns:
        空 dict（灌库副作用写入 Chroma，不更新 state 字段）。
    """
    chunks = state.get("chunks", [])
    client = make_persistent_client(CHROMA_PATH)
    upsert_chunks(client, chunks)
    return {}


async def llm_index_node(state: IndexAgentState) -> dict[str, Any]:
    """LLM 索引节点：让 LLM 看现有索引 + 本批切片，输出更新后的完整索引表。

    Args:
        state: 图状态，需含 existing_rows 与 chunks。

    Returns:
        更新 index_update（IndexUpdate 对象）。
    """
    existing: list[IndexRow] = state.get("existing_rows", [])
    chunks = state.get("chunks", [])
    update = await update_index_via_llm(_llm, existing, chunks)
    return {"index_update": update}


def write_index_node(state: IndexAgentState) -> dict[str, Any]:
    """写回节点：把 LLM 输出的索引表覆盖写回 data/index.md。

    Args:
        state: 图状态，需含 index_update。

    Returns:
        空 dict（写文件副作用，不更新 state 字段）。
    """
    update = state.get("index_update")
    if update is not None:
        write_index(INDEX_MD_PATH, update.rows)
    return {}


def build_index_graph() -> Any:
    """构建并编译 Index Agent 图。

    Returns:
        编译后的 LangGraph 可执行图（StateGraph 编译产物）。
    """
    g = StateGraph(IndexAgentState)
    g.add_node("scan", scan_node)
    g.add_node("chunk", chunk_node)
    g.add_node("embed", embed_node)
    g.add_node("llm_index", llm_index_node)
    g.add_node("write_index", write_index_node)
    g.add_edge(START, "scan")
    g.add_edge("scan", "chunk")
    g.add_edge("chunk", "embed")
    g.add_edge("embed", "llm_index")
    g.add_edge("llm_index", "write_index")
    g.add_edge("write_index", END)
    return g.compile(name="IndexAgent")


# 注册为 langgraph.json 可发现的 graph（供 SDK 测试 / Studio 调试）
graph = build_index_graph()
