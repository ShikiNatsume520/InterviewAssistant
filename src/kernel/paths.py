"""项目路径常量（唯一真相源）。

集中 ``PROJECT_ROOT`` 与各数据目录路径，替换散落在 agent 内的
``Path(__file__).parent...`` 推算（R0 前有 3 处各自推算）。
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent
"""项目根目录（``src/kernel/`` 的上三级）。"""

MARKDOWN_DIR: Path = PROJECT_ROOT / "data" / "markdown"
"""本地 markdown 知识库目录。"""

RESUMES_DIR: Path = PROJECT_ROOT / "data" / "resumes"
"""用户简历目录（lapis-cv 格式 markdown，``resume_agent`` 据此读写）。"""

CHROMA_PATH: Path = PROJECT_ROOT / "data" / "chroma"
"""Chroma 向量库持久化目录。"""

INDEX_MD_PATH: Path = PROJECT_ROOT / "data" / "index.md"
"""LLM 维护的文件级语义索引文件路径。"""

LOGS_DIR: Path = PROJECT_ROOT / "logs"
"""调试日志目录（``debug_YYYYMMDD_HHMMSS.log`` 落盘于此）。"""
