"""网页爬取工具 —— httpx 抓取 + trafilatura 提正文。

对外接口:
- ``fetch_text(url)`` — 抓取 URL 并提取正文，返回 ``(title, text)``；失败返回 ``("", "")``。

trafilatura 专为网页正文提取，质量优于手写 BS4（原型 phase6_ddgs_probe.py
实测：从 LangGraph 官方页提取 1761 字符正文）。所有异常捕获返回空 —— 失败的
URL 在 ``search_node`` 里被跳过，不中断整条深研链路。
"""

from __future__ import annotations

_DEFAULT_TIMEOUT = 15.0
"""单页抓取超时（秒）。"""

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
"""伪装浏览器 UA，避免被部分站点拒识别为爬虫。"""


def fetch_text(url: str) -> tuple[str, str]:
    """抓取 URL 并用 trafilatura 提取正文。

    Args:
        url: 目标网页 URL。

    Returns:
        ``(title, text)`` 二元组。失败（httpx/trafilatura 不可用、HTTP 非 2xx、
        提取为空、JS 渲染页）任一情况均返回 ``("", "")``，由调用方跳过。
    """
    try:
        import httpx
    except ImportError:  # pragma: no cover
        return "", ""
    try:
        import trafilatura
    except ImportError:  # pragma: no cover
        return "", ""

    try:
        resp = httpx.get(
            url,
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
    except Exception:  # noqa: BLE001 - 网络失败跳过该 URL
        return "", ""
    if resp.status_code >= 400:
        return "", ""

    html = resp.text
    try:
        text = trafilatura.extract(html, include_comments=False, include_tables=False)
    except Exception:  # noqa: BLE001 - 提取失败跳过
        return "", ""
    if not text or not text.strip():
        return "", ""

    title = ""
    try:
        meta = trafilatura.extract(html, output_format="json", with_metadata=True)
        if meta:
            import json

            obj = json.loads(meta)
            title = str(obj.get("title") or "")
    except Exception:  # noqa: BLE001 - 标题缺失不影响正文
        pass

    return title, text
