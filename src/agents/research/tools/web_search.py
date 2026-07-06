"""Web 搜索工具 —— ddgs（DuckDuckGo）封装。

仅提供两个对外接口:
- ``search(query, max_results)`` — 返回 ``[SearchResult]``（title/href/body）。
- ``connectivity_probe()`` — 一次极简查询判连通性，``True`` 表示可达。

ddgs 版本: 9.x（``from ddgs import DDGS``，``d.text(q, max_results=N)`` 返回
``[{title, href, body}]``，原型 phase6_ddgs_probe.py 实测确认）。

所有调用捕获异常返回空/False —— 失败由子图的 ``connectivity_check_node`` 节点
经 interrupt 重试处理，工具自身不抛。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from collections.abc import Iterable


def _get_ddgs() -> Any:
    """惰性获取 DDGS 类（避免 mypy 全局类型推断冲突）。"""
    try:
        from ddgs import DDGS

        return DDGS
    except ImportError:  # pragma: no cover - 依赖缺失时给出明确报错
        return None


class SearchResult(TypedDict):
    """单条搜索结果。

    Attributes:
        title: 结果标题。
        href: 结果 URL（ddgs 9.x 字段名为 href）。
        body: 结果摘要。
    """

    title: str
    href: str
    body: str


def search(query: str, max_results: int = 3) -> list[SearchResult]:
    """用 DuckDuckGo 搜索，返回 ``SearchResult`` 列表。

    Args:
        query: 检索词。
        max_results: 最多返回条数，默认 3。

    Returns:
        ``SearchResult`` 列表；ddgs 不可用或查询失败时返回空列表。
    """
    ddgs_cls = _get_ddgs()
    if ddgs_cls is None:
        return []
    try:
        with ddgs_cls() as d:
            raw: Iterable[dict[str, object]] = d.text(query, max_results=max_results)
    except Exception:  # noqa: BLE001 - 网络失败交由上层重试，工具不抛
        return []
    out: list[SearchResult] = []
    for item in raw or []:
        href = str(item.get("href") or item.get("url") or "")
        if not href:
            continue
        out.append(
            SearchResult(
                title=str(item.get("title") or ""),
                href=href,
                body=str(item.get("body") or ""),
            )
        )
    return out


def connectivity_probe() -> bool:
    """一次极简查询，判 DuckDuckGo 是否连通。

    用 ``d.text("test", max_results=1)`` 探测：返回非空即视为连通。

    Returns:
        连通返回 True；ddgs 不可用 / 抛异常 / 返回空均视为不通返回 False。
    """
    ddgs_cls = _get_ddgs()
    if ddgs_cls is None:
        return False
    try:
        with ddgs_cls() as d:
            return bool(d.text("test", max_results=1))
    except Exception:  # noqa: BLE001 - 探测失败统一判不通
        return False
