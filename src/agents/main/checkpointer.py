"""Checkpointer 入口（薄封装）。

逻辑已迁至 ``kernel.persistence.generate_checkpointer``（R0 重构）。本模块保留
仅为 ``langgraph.json`` 的 checkpointer 路径仍指向
``./src/agent/checkpointer.py:generate_checkpointer``；R3 物理重排时将更新
langgraph.json 指向新路径或直接指向 kernel。
"""

from __future__ import annotations

from kernel.persistence import generate_checkpointer  # noqa: F401

__all__ = ["generate_checkpointer"]
