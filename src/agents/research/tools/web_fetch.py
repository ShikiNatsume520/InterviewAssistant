"""网页爬取工具 —— httpx 抓取 + trafilatura 提正文。

对外接口:
- ``fetch_text(url)`` — 抓取 URL 并提取正文，返回 ``(title, text)``；失败返回 ``("", "")``。

trafilatura 专为网页正文提取，质量优于手写 BS4。所有异常捕获返回空 —— 失败的
URL 在 ``search_node`` 里被跳过，不中断整条深研链路。

兼容性要点（原型 ``prototypes/web_fetch_compat_probe.py`` 实测）:
- TUN/代理环境下 httpx 默认（HTTP/2 协商）对部分站点（如 google）会 read timeout，
  强制 ``http2=False``（HTTP/1.1）更稳。dev.to/duckduckgo 在 http1 下稳定 200。
- 用 ``httpx.Client`` 复用连接 + 单次重试，减少偶发 TLS EOF。
"""

from __future__ import annotations

import httpx

_DEFAULT_TIMEOUT = 20.0
"""单页抓取超时（秒）。TUN 下适当放宽。"""

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
"""伪装浏览器 UA，避免被部分站点拒识别为爬虫。"""

# 模块级 Client 单例（http2=False 复用连接，避免每次 new client 触发 TLS 握手）
_fetch_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    """获取（惰性创建并复用的）httpx Client。

    ``http2=False`` 强制 HTTP/1.1——TUN/代理环境下 HTTP/2 协商对部分站点会
    read timeout，HTTP/1.1 更稳（原型实测）。复用连接减少 TLS 握手开销。
    """
    global _fetch_client
    if _fetch_client is None:
        _fetch_client = httpx.Client(
            http2=False,
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
    return _fetch_client


def fetch_text(url: str) -> tuple[str, str]:
    """抓取 URL 并用 trafilatura 提取正文。

    Args:
        url: 目标网页 URL。

    Returns:
        ``(title, text)`` 二元组。失败（trafilatura 不可用、HTTP 非 2xx、
        提取为空、JS 渲染页、TUN 偶发 TLS EOF）任一情况均返回 ``("", "")``，
        由调用方跳过。单次失败会重试一次（TUN 偶发超时常见，重试即恢复）。
    """
    try:
        import trafilatura
    except ImportError:  # pragma: no cover
        return "", ""

    client = _get_client()
    html = ""
    for attempt in (1, 2):  # 最多 2 次，TUN 偶发超时重试即恢复
        try:
            resp = client.get(url)
            if resp.status_code < 400:
                html = resp.text
                break
            dlog_fetch(url, resp.status_code, attempt)
            if attempt == 2 or resp.status_code >= 400:
                return "", ""
        except Exception:  # noqa: BLE001 - TLS EOF/超时等重试
            if attempt == 2:
                return "", ""
            continue
    if not html:
        return "", ""

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


def dlog_fetch(url: str, status: int, attempt: int) -> None:
    """记录抓取失败（轻量日志，避免引入 dlog 循环依赖）。"""
    try:
        from kernel.logging import dlog

        dlog(
            "research.fetch",
            "fetch_text",
            "HTTP 非 2xx 或失败",
            url=url,
            status=status,
            attempt=attempt,
        )
    except Exception:  # noqa: BLE001
        pass
