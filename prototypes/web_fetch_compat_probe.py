"""web_fetch 兼容性诊断原型。

目标：找一个能稳定获取 dev.to / duckduckgo 等页面正文的 fetch 方式。

逐个尝试：
1. httpx 默认（当前 web_fetch 实现）
2. httpx http2=False（强制 HTTP/1.1）
3. httpx + trust_env（走系统代理环境变量）
4. requests（urllib3 TLS 实现，往往更兼容 TUN）
5. requests + session（连接复用）

每种方式打印 status / len / err。成功的最后用 trafilatura 提正文验证。
"""

from __future__ import annotations

import json

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
URLS = [
    "https://dev.to/erikch/loop-engineering-do-frontend-and-fullstack-devs-actually-need-it-48eb",
    "https://www.google.com",
    "https://duckduckgo.com",
]


def try_httpx_default(url: str) -> tuple[int, str]:
    import httpx

    try:
        r = httpx.get(url, timeout=15, follow_redirects=True, headers={"User-Agent": UA})
        return r.status_code, f"len={len(r.text)}"
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


def try_httpx_http1(url: str) -> tuple[int, str]:
    import httpx

    try:
        with httpx.Client(
            http2=False,
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": UA},
            trust_env=True,
        ) as c:
            r = c.get(url)
            return r.status_code, f"len={len(r.text)}"
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


def try_httpx_h2(url: str) -> tuple[int, str]:
    import httpx

    try:
        with httpx.Client(
            http2=True,
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": UA},
        ) as c:
            r = c.get(url)
            return r.status_code, f"len={len(r.text)} h2={r.http_version}"
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


def try_requests(url: str) -> tuple[int, str]:
    try:
        import requests

        r = requests.get(url, timeout=15, headers={"User-Agent": UA})
        return r.status_code, f"len={len(r.text)}"
    except ImportError:
        return -1, "requests 未安装"
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


def try_requests_session(url: str) -> tuple[int, str]:
    try:
        import requests
        from requests.adapters import HTTPAdapter

        s = requests.Session()
        s.mount("https://", HTTPAdapter(max_retries=2))
        r = s.get(url, timeout=15, headers={"User-Agent": UA})
        return r.status_code, f"len={len(r.text)}"
    except ImportError:
        return -1, "requests 未安装"
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


def main() -> None:
    methods = [
        ("httpx 默认", try_httpx_default),
        ("httpx http2=False+trust_env", try_httpx_http1),
        ("httpx http2=True", try_httpx_h2),
        ("requests", try_requests),
        ("requests session", try_requests_session),
    ]
    for url in URLS:
        print(f"\n=== {url} ===")
        for name, fn in methods:
            code, info = fn(url)
            mark = "✓" if code == 200 else "✗"
            print(f"  {mark} [{name:<30}] {code}  {info}")

    # 用第一个成功的 httpx 方式提正文验证 trafilatura
    print("\n=== trafilatura 正文提取验证（dev.to）===")
    url = URLS[0]
    html = None
    try:
        import httpx

        with httpx.Client(
            http2=False, timeout=15, follow_redirects=True, headers={"User-Agent": UA}
        ) as c:
            html = c.get(url).text
    except Exception as e:  # noqa: BLE001
        print(f"  httpx 取 html 失败: {e}")
    if html:
        try:
            import trafilatura

            text = trafilatura.extract(html, include_comments=False, include_tables=False)
            print(f"  正文: {'空' if not text else text[:200]!r}")
            meta = trafilatura.extract(html, output_format="json", with_metadata=True)
            if meta:
                obj = json.loads(meta)
                print(f"  标题: {obj.get('title')!r}")
        except ImportError:
            print("  trafilatura 未安装")
        except Exception as e:  # noqa: BLE001
            print(f"  trafilatura 异常: {e}")


if __name__ == "__main__":
    main()
