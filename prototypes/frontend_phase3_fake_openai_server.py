"""阶段 3 本地联调专用 OpenAI-compatible 假端点，不访问外网。"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar


class Handler(BaseHTTPRequestHandler):
    """返回固定 Chat Completions 响应。"""

    protocol_version = "HTTP/1.1"
    request_count: ClassVar[int] = 0

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        type(self).request_count += 1
        messages = request.get("messages", [])
        joined = "\n".join(str(message.get("content", "")) for message in messages)
        content = (
            "[]"
            if "JSON" in joined and "事实" in joined
            else "这是来自本地假模型的阶段 3 联调回复。"
        )
        if request.get("stream"):
            pieces = [content]
            if "慢速" in joined and content != "[]":
                split_at = max(1, len(content) // 2)
                pieces = [content[:split_at], content[split_at:]]
            chunks = [
                {
                    "id": f"mock-{type(self).request_count}",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "mock-chat",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": piece},
                            "finish_reason": None,
                        }
                    ],
                }
                for piece in pieces
            ] + [
                {
                    "id": f"mock-{type(self).request_count}",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "mock-chat",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
            ]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            for index, chunk in enumerate(chunks):
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
                if len(pieces) > 1 and index == 0:
                    time.sleep(15)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            self.close_connection = True
            return
        body = json.dumps(
            {
                "id": f"mock-{type(self).request_count}",
                "object": "chat.completion",
                "created": 1,
                "model": "mock-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8011), Handler).serve_forever()
