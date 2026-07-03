# src/agent/checkpointer.py
import contextlib

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


@contextlib.asynccontextmanager
async def generate_checkpointer():
    # 💡 强行指定和测试脚本一模一样的本地数据库路径
    async with AsyncSqliteSaver.from_conn_string("./state_db.sqlite") as saver:
        yield saver