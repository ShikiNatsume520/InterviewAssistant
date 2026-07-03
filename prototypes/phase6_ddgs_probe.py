"""Phase 6 原型 1: ddgs 可达性 + httpx/trafilatura 爬取链路探测。

验证 phase 6 最高风险点 —— web 搜索方案能否落地:
  A. ddgs 在当前网络环境下能否返回搜索结果（梯子已挂）。
  B. 一次极简查询能否作为"连通性探测"的判通依据（图里 connectivity_check 用）。
  C. httpx 抓取 + trafilatura 提正文能否拿到干净文本。
  D. 搜索结果字段名（不同 ddgs 版本: title/href/url/content），确认实际形态。

运行:
    .venv/Scripts/python.exe prototypes/phase6_ddgs_probe.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _sep(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def probe_ddgs_import() -> object | None:
    """A. 探测 ddgs 导入路径与版本。"""
    _sep("A. ddgs 导入探测")
    try:
        import ddgs
    except ImportError:
        print("[FAIL] 无法 import ddgs —— 未安装? 请 `pip install ddgs`")
        return None
    ver = getattr(ddgs, "__version__", "?")
    print(f"[OK] import ddgs, version={ver}")
    try:
        from ddgs import DDGS
    except ImportError:
        print("[FAIL] 无法 from ddgs import DDGS")
        return None
    print("[OK] from ddgs import DDGS")
    return DDGS


def probe_ddgs_text(DDGS: type) -> dict | None:
    """B/C. 实际搜索 + 连通性探测依据 + 字段名确认。

    返回首条结果 dict（供 D 爬取其 URL），失败返回 None。
    """
    _sep("B. ddgs.text() 实际查询")
    queries = ["LangGraph StateGraph", "Python asyncio"]
    for q in queries:
        print(f"\n--- 查询: {q!r} ---")
        try:
            d = DDGS()
            r = d.text(q, max_results=3)
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] 抛异常: {type(e).__name__}: {e}")
            continue
        if not r:
            print("[WARN] 返回空列表（可能被限流/网络不通）")
            continue
        print(f"[OK] 返回 {len(r)} 条")
        first = r[0]
        print(f"    字段名: {list(first.keys())}")
        title = first.get("title", "")
        href = first.get("href") or first.get("url") or ""
        body = first.get("body") or first.get("content") or ""
        print(f"    title : {title[:70]}")
        print(f"    href  : {href}")
        print(f"    body  : {body[:100]}")
        if href:
            return first
    print("\n[FAIL] 两次查询均无结果 —— ddgs 不可达，需换搜索方案")
    return None


def probe_connectivity_check(DDGS: type) -> bool:
    """C. 验证"一次极简查询判通断"的写法（图里 connectivity_check_node 用）。"""
    _sep("C. 连通性探测写法验证（极简查询判通断）")
    try:
        d = DDGS()
        r = d.text("test", max_results=1)
        ok = bool(r)
        print(f"[{'OK' if ok else 'WARN'}] 极简查询返回 {len(r)} 条 → 判定连通={ok}")
        return ok
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] 极简查询抛异常 → 判定不通: {type(e).__name__}: {e}")
        return False


def probe_fetch_trafilatura(url: str) -> None:
    """D. httpx 抓取 + trafilatura 提正文。"""
    _sep("D. httpx + trafilatura 爬取正文")
    try:
        import httpx
    except ImportError:
        print("[FAIL] 无法 import httpx —— 未安装? 请 `pip install httpx`")
        return
    try:
        import trafilatura
    except ImportError:
        print("[FAIL] 无法 import trafilatura —— 未安装? 请 `pip install trafilatura`")
        return

    print(f"目标 URL: {url}")
    try:
        resp = httpx.get(url, timeout=15.0, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] httpx 抓取抛异常: {type(e).__name__}: {e}")
        return
    print(f"[OK] httpx status={resp.status_code}, len={len(resp.content)} bytes")
    html = resp.text
    try:
        text = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] trafilatura.extract 抛异常: {type(e).__name__}: {e}")
        return
    if not text.strip():
        print("[WARN] trafilatura 提取为空（可能是 JS 渲染页/非文章页）")
        return
    print(f"[OK] 提取正文 {len(text)} 字符")
    print(f"    预览: {text[:200].replace(chr(10), ' ')}…")


def main() -> None:
    print("Phase 6 原型 1: ddgs + 爬取链路探测")
    DDGS = probe_ddgs_import()
    if DDGS is None:
        return
    probe_connectivity_check(DDGS)
    first = probe_ddgs_text(DDGS)
    if first is None:
        print("\n[结论] ddgs 不可达 —— phase 6 搜索方案需重新协商（换 Tavily/国内方案）")
        return
    href = first.get("href") or first.get("url") or ""
    if href:
        probe_fetch_trafilatura(href)
    _sep("结论")
    print("ddgs 可达 → phase 6 搜索方案可行")
    print("下一步: 原型 2 验证子图内连通性重试 + 跨子图调用 index_agent")


if __name__ == "__main__":
    main()
