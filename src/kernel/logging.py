"""调试日志工具（dlog）—— 统一落盘 + 控制台双写。

为零依赖模块（不 import 任何 ``agent.*``），保证子图 import 本模块不触发
``agent`` 包初始化、不形成循环依赖。

输出目的地：
- **stderr**（uvicorn 控制台实时可见）。
- **文件** ``logs/debug_YYYYMMDD_HHMMSS.log``（进程启动时按时间生成新文件）。
  两者双写，文件为排查问题的持久记录。

格式与缩进：
- 每条日志以 ``[{scope}:{node}]`` 开头标明位置（哪个图/哪个节点）。
- 按 scope 层级自动缩进，形成视觉层次：
  - ``main`` 顶格；``main.xxx`` 缩进 2 空格。
  - 子图（rag/resume/research/index）缩进 2 空格；其内部函数（``xxx.yyy``）4 空格。

开关：环境变量 ``IA_DEBUG=1`` 启用（默认启用）；设为 0 则不写日志、不创建文件。
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from typing import Any

from kernel.paths import LOGS_DIR

_INITIALIZED = False
_ENABLED: bool = os.getenv("IA_DEBUG", "1") not in ("0", "", "false", "False")
_LOG_FILE: str | None = None


def _indent_for(scope: str) -> str:
    """按 scope 层级返回缩进前缀，让日志有视觉层次。

    - ``main`` → 顶格；``main.xxx`` → 2 空格
    - 子图（rag/resume/research/index）→ 2 空格；其内部函数（``xxx.yyy``）→ 4 空格
    """
    top = scope.split(".", 1)[0]
    has_dot = "." in scope
    if top == "main":
        return "" if not has_dot else "  "
    return "  " if not has_dot else "    "


def _ensure_logger() -> logging.Logger:
    """返回统一的调试 logger（首次调用时配置 stderr + 文件双 handler）。"""
    global _INITIALIZED, _LOG_FILE
    logger = logging.getLogger("ia.debug")
    if not _INITIALIZED:
        level = logging.DEBUG if _ENABLED else logging.WARNING
        logger.setLevel(level)
        if not logger.handlers:
            fmt = logging.Formatter(
                "%(asctime)s [%(name)s] %(message)s",
                datefmt="%H:%M:%S",
            )
            sh = logging.StreamHandler(sys.stderr)
            sh.setFormatter(fmt)
            logger.addHandler(sh)
            if _ENABLED:
                LOGS_DIR.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                _LOG_FILE = str(LOGS_DIR / f"debug_{ts}.log")
                fh = logging.FileHandler(_LOG_FILE, encoding="utf-8")
                fh.setFormatter(fmt)
                logger.addHandler(fh)
        logger.propagate = False
        _INITIALIZED = True
    return logger


def dlog(scope: str, node: str, msg: str, **extra: Any) -> None:
    """输出一条调试日志（stderr + 文件双写）。

    Args:
        scope: 图/模块作用域，如 ``"main"`` / ``"rag"`` / ``"rag.retrieval"``。
            scope 决定缩进层级（见 ``_indent_for``）。
        node: 节点/函数名，如 ``"chat_node"`` / ``"semantic_retrieve"``。
        msg: 日志正文。
        **extra: 附加键值对，以 ``k=v`` 追加（值经 ``_short`` 截断）。
    """
    if not _ENABLED:
        return
    logger = _ensure_logger()
    head = f"{_indent_for(scope)}[{scope}:{node}] {msg}"
    tail = ""
    if extra:
        parts: list[str] = [f"{k}={_short(v)}" for k, v in extra.items()]
        tail = " | " + " ".join(parts)
    logger.debug("%s%s", head, tail)


def _short(v: Any, limit: int = 120) -> str:
    """把任意值转成短字符串（截断超长内容）。

    list/tuple 显示前 3 项摘要 + 总数；dict 显示前 3 个 key；str 截断并转义换行。
    """
    if isinstance(v, str):
        s = v.replace("\n", "\\n").replace("\r", "")
        return s if len(s) <= limit else s[:limit] + "…"
    if isinstance(v, (list, tuple)):
        items = [_short(x, 40) for x in v[:3]]
        suffix = "" if len(v) <= 3 else f",…({len(v)})"
        return f"[{', '.join(items)}{suffix}]"
    if isinstance(v, dict):
        keys = ",".join(str(k) for k in list(v.keys())[:3])
        suffix = "..." if len(v) > 3 else ""
        return f"{{{' '.join([keys, suffix]).strip()}}}"
    return repr(v)[:limit]


def summarize_messages(messages: list[Any]) -> str:
    """把消息列表摘要成短串，用于日志。

    形如 ``[U:你好, AI(tool=rag_agent), T:..., AI:回复...]``。
    """
    if not messages:
        return "[]"
    parts: list[str] = []
    for m in messages[-8:]:  # 只看最后 8 条
        mtype = getattr(m, "type", "") or (
            m.get("type") if isinstance(m, dict) else "?"
        )
        if mtype == "human":
            c = _msg_content(m)
            parts.append(f"U:{_short(c, 30)}")
        elif mtype == "ai":
            tcs = getattr(m, "tool_calls", []) or []
            c = _msg_content(m)
            if tcs:
                names = ",".join(t.get("name", "?") for t in tcs)
                parts.append(f"AI(tool={names})")
            else:
                parts.append(f"AI:{_short(c, 30)}")
        elif mtype == "tool":
            c = _msg_content(m)
            parts.append(f"T:{_short(c, 30)}")
        else:
            parts.append(str(mtype))
    prefix = "..." if len(messages) > 8 else ""
    return f"{prefix}[{', '.join(parts)}]"


def _msg_content(m: Any) -> str:
    """安全提取消息 content 为字符串。"""
    c = getattr(m, "content", "")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        out: list[str] = []
        for part in c:
            if isinstance(part, dict) and part.get("type") == "text":
                out.append(str(part.get("text", "")))
        return " ".join(out)
    return str(c)
