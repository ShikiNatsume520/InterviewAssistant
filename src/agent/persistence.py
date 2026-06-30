"""持久化层 — Checkpointer + Store 工厂函数。

Phase 4 新增，统一管理 ``SqliteSaver``（短期记忆断点）和
``SqliteStore``（长期记忆画像）的实例创建。
"""

from __future__ import annotations

import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.sqlite import SqliteStore


def get_checkpointer(db_path: str = "sqlite_checkpoints.db") -> SqliteSaver:
    """创建 ``SqliteSaver`` 实例（连接到本地 SQLite 文件）。

    Args:
        db_path: SQLite 数据库文件路径，默认为 ``sqlite_checkpoints.db``。

    Returns:
        SqliteSaver 实例。
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    return SqliteSaver(conn)


STORE_DB_PATH: str = "sqlite_store.db"
"""Store 持久化文件路径。"""


def get_store(db_path: str = STORE_DB_PATH) -> SqliteStore:
    """创建 ``SqliteStore`` 实例（连接到本地 SQLite 文件）。

    Args:
        db_path: SQLite 数据库文件路径，默认为 ``sqlite_store.db``。

    Returns:
        SqliteStore 实例（已调用 ``setup()`` 初始化表结构）。
    """
    conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
    store = SqliteStore(conn)
    store.setup()
    return store
