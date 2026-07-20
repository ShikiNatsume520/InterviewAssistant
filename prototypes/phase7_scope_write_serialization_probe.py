"""阶段 7 原型：验证同一知识作用域串行写、不同作用域并行。"""

from __future__ import annotations

import json
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path


class ScopeLocks:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)

    def get(self, scope_key: str) -> threading.Lock:
        with self._guard:
            return self._locks[scope_key]


def update_index(path: Path, resource_id: str, lock: threading.Lock) -> None:
    """模拟 Index Agent 的读取完整索引、计算、覆盖写回。"""
    with lock:
        rows = json.loads(path.read_text(encoding="utf-8"))
        time.sleep(0.03)
        rows.append(resource_id)
        path.write_text(json.dumps(rows), encoding="utf-8")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ia-phase7-scope-lock-") as tmp:
        root = Path(tmp)
        locks = ScopeLocks()
        alice_index = root / "alice-index.json"
        bob_index = root / "bob-index.json"
        alice_index.write_text("[]", encoding="utf-8")
        bob_index.write_text("[]", encoding="utf-8")

        workers = [
            threading.Thread(
                target=update_index,
                args=(alice_index, f"alice-{number}", locks.get("personal:guest-a")),
            )
            for number in range(6)
        ]
        workers += [
            threading.Thread(
                target=update_index,
                args=(bob_index, f"bob-{number}", locks.get("personal:guest-b")),
            )
            for number in range(3)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()

        alice_rows = set(json.loads(alice_index.read_text(encoding="utf-8")))
        bob_rows = set(json.loads(bob_index.read_text(encoding="utf-8")))
        assert alice_rows == {f"alice-{number}" for number in range(6)}
        assert bob_rows == {f"bob-{number}" for number in range(3)}
        assert locks.get("personal:guest-a") is not locks.get("personal:guest-b")

    print("PASS: 同一 principal 的完整索引覆盖写被串行化，不丢失更新。")
    print("PASS: 不同 principal 使用不同锁，可以并行维护各自索引。")


if __name__ == "__main__":
    main()
