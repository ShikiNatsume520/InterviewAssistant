"""调试日志工具（Phase 5）。

为追踪主图 / 子图节点运行过程提供统一入口。所有日志走 ``logging``，
格式为 ``[graph:node]`` 前缀，便于在 uvicorn 输出中辨识。

开关：环境变量 ``IA_DEBUG=1`` 启用详细日志（默认启用）。
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

_INITIALIZED = False
_ENABLED: bool = os.getenv("IA_DEBUG", "1") not in ("0", "", "false", "False")


def _ensure_logger() -> logging.Logger:
    """返回统一的调试 logger（首次调用时配置）。"""
    global _INITIALIZED
    logger = logging.getLogger("ia.debug")
    if not _INITIALIZED:
        level = logging.DEBUG if _ENABLED else logging.WARNING
        logger.setLevel(level)
        # 避免重复 handler
        if not logger.handlers:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(name)s] %(message)s",
                    datefmt="%H:%M:%S",
                )
            )
            logger.addHandler(handler)
        logger.propagate = False
        _INITIALIZED = True
    return logger


def dlog(scope: str, node: str, msg: str, **extra: Any) -> None:
    """输出一条调试日志。

    Args:
        scope: 图作用域，如 ``"main"`` / ``"rag"`` / ``"resume"``。
        node: 节点名，如 ``"chat_node"`` / ``"plan_node"``。
        msg: 日志正文。
        **extra: 附加键值对，会以 ``k=v`` 形式追加。
    """
    if not _ENABLED:
        return
    logger = _ensure_logger()
    tail = ""
    if extra:
        parts: list[str] = []
        for k, v in extra.items():
            sv = _short(v)
            parts.append(f"{k}={sv}")
        tail = " | " + " ".join(parts)
    logger.debug("[%s:%s] %s%s", scope, node, msg, tail)


def _short(v: Any, limit: int = 120) -> str:
    """把任意值转成短字符串（截断超长内容）。"""
    if isinstance(v, str):
        s = v.replace("\n", "\\n").replace("\r", "")
        return s if len(s) <= limit else s[:limit] + "…"
    if isinstance(v, (list, tuple)):
        return f"[{len(v)} items]"
    if isinstance(v, dict):
        keys = ",".join(v.keys())
        return f"{{{' '.join(keys[:3])}{'...' if len(v)>3 else ''}}}"
    return repr(v)[:limit]


def summarize_messages(messages: list[Any]) -> str:
    """把消息列表摘要成短串，用于日志。

    形如 ``[U:你好, AI(tool=rag_agent), T:..., AI:回复...]``。
    """
    if not messages:
        return "[]"
    parts: list[str] = []
    for m in messages[-8:]:  # 只看最后 8 条
        mtype = getattr(m, "type", "") or (m.get("type") if isinstance(m, dict) else "?")
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
