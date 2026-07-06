"""持久化层 — Checkpointer + Store 工厂与路径常量（唯一真相源）。

合并原 ``agent/persistence.py``（sync Store）与 ``agent/checkpointer.py``
（async Checkpointer）逻辑（R0 上提到 kernel）。Studio（``langgraph dev``）
与 FastAPI server 共用同一组路径常量，消除三套 db 文件并存
（``state_db.sqlite`` / ``sqlite_store.db`` / ``sqlite_checkpoints.db``）。

路径基于 ``kernel.paths.PROJECT_ROOT``，不依赖 cwd。db 文件统一放在
``data/state/`` 下，与知识库（``data/markdown`` / ``data/chroma``）分开。
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.sqlite import SqliteStore

from kernel.paths import PROJECT_ROOT

CHECKPOINT_DB_PATH: Path = PROJECT_ROOT / "data" / "state" / "checkpoints.sqlite"
"""主图断点数据库（Studio 与 server 共用，支持 thread_id 状态回放）。"""

STORE_DB_PATH: Path = PROJECT_ROOT / "data" / "state" / "store.sqlite"
"""长期记忆 Store 数据库（按 user_id 键控）。"""


def _ensure_parent(path: Path) -> None:
    """确保 db 文件所在目录存在（SqliteSaver/SqliteStore 不会自动建目录）。"""
    path.parent.mkdir(parents=True, exist_ok=True)


@contextlib.asynccontextmanager
async def generate_checkpointer() -> AsyncIterator[AsyncSqliteSaver]:
    """创建 ``AsyncSqliteSaver``（``langgraph.json`` checkpointer 入口）。

    供 ``langgraph dev``（Studio）与 server 复用——两者都连 ``CHECKPOINT_DB_PATH``。
    """
    _ensure_parent(CHECKPOINT_DB_PATH)
    async with AsyncSqliteSaver.from_conn_string(str(CHECKPOINT_DB_PATH)) as saver:
        yield saver


def get_store(db_path: Path | str | None = None) -> SqliteStore:
    """创建 ``SqliteStore`` 实例（已 ``setup()`` 初始化表结构）。

    Args:
        db_path: SQLite 数据库文件路径，默认 ``STORE_DB_PATH``。

    Returns:
        SqliteStore 实例。
    """
    path = Path(db_path) if db_path is not None else STORE_DB_PATH
    _ensure_parent(path)
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    store = SqliteStore(conn)
    store.setup()
    return store
