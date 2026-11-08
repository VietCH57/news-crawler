"""Nơi ghi bản tin. Hiện có FileSink; sau này thêm KafkaSink với cùng giao diện."""

from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import socket
from pathlib import Path
from typing import Any, Protocol

from .timeutil import from_iso, now

log = logging.getLogger(__name__)


class Sink(Protocol):
    def write(self, record: dict[str, Any]) -> None: ...
    def close(self) -> None: ...


class FileSink:
    """Ghi JSON Lines theo giờ thu thập: ``landing/articles/dt=YYYY-MM-DD/hour=HH/``.

    Bản tin được ghi vào ``*.jsonl.tmp`` (chưa nén) và flush ngay. Sang giờ mới,
    hoặc khi tắt chương trình, file tạm được nén thành ``*.jsonl.gz``.
    Lúc khởi động, file tạm còn sót do máy tắt đột ngột được nén nốt.
    """

    def __init__(self, root: Path, stream: str = "articles", fsync: bool = False, on_rotate=None):
        self.base = root / "landing" / stream
        self.base.mkdir(parents=True, exist_ok=True)
        self.fsync = fsync
        self.host = socket.gethostname().split(".")[0].replace(" ", "-") or "host"
        self.on_rotate = on_rotate  # callback(path, size_bytes, n_records)
        self._fh = None
        self._tmp: Path | None = None
        self._key: tuple[str, str] | None = None
        self._count = 0
        self.recover()

    def _dir_for(self, dt: str, hour: str) -> Path:
        return self.base / f"dt={dt}" / f"hour={hour}"

    def recover(self) -> None:
        for tmp in sorted(self.base.rglob("*.jsonl.tmp")):
            if tmp != self._tmp:
                log.info("Nén file tạm còn sót: %s", tmp)
                self._compress(tmp)

    def write(self, record: dict[str, Any]) -> None:
        crawled = from_iso(record["crawled_at"]) if record.get("crawled_at") else now()
        key = (crawled.strftime("%Y-%m-%d"), crawled.strftime("%H"))
        if key != self._key:
            self._rotate(key)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        self._fh.write(line + "\n")
        self._fh.flush()
        if self.fsync:
            os.fsync(self._fh.fileno())
        self._count += 1

    def _rotate(self, key: tuple[str, str]) -> None:
        self._close_current()
        d = self._dir_for(*key)
        d.mkdir(parents=True, exist_ok=True)
        stamp = now().strftime("%Y%m%d%H%M%S")
        self._tmp = d / f"part-{self.host}-{stamp}-{os.getpid()}.jsonl.tmp"
        self._fh = open(self._tmp, "a", encoding="utf-8", newline="\n")
        self._key = key
        self._count = 0

    def _close_current(self) -> None:
        if self._fh is None:
            return
        self._fh.close()
        self._fh = None
        if self._tmp is not None:
            self._compress(self._tmp, self._count)
        self._tmp = None
        self._key = None

    def _compress(self, tmp: Path, count: int | None = None) -> Path | None:
        if not tmp.exists():
            return None
        if tmp.stat().st_size == 0:
            tmp.unlink()
            return None
        final = tmp.with_suffix(".gz")  # part-x.jsonl.tmp -> part-x.jsonl.gz
        with open(tmp, "rb") as src, gzip.open(final, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst)
        tmp.unlink()
        size = final.stat().st_size
        if count is None:
            with gzip.open(final, "rt", encoding="utf-8") as f:
                count = sum(1 for _ in f)
        log.info("Đã ghi %s (%d bản tin, %.1f KB)", final.name, count, size / 1024)
        if self.on_rotate:
            self.on_rotate(final, size, count)
        return final

    def close(self) -> None:
        self._close_current()
