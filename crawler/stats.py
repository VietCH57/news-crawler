"""Thống kê hằng ngày theo từng báo, ghi ra ``data/stats/YYYY-MM-DD.json``.

Đây là nguồn số liệu thật (số bài, dung lượng) để cập nhật đề xuất.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from .timeutil import now, now_iso

log = logging.getLogger(__name__)

COUNTERS = (
    "feed_items",          # số mục đọc được từ RSS
    "discovered",          # số URL mới đưa vào hàng đợi
    "fetched",             # số trang đã tải
    "written",             # số bản tin đã ghi
    "parse_failed",        # parse_ok = false
    "fallback_used",       # bố cục lạ, phải dùng trafilatura
    "short_content",       # bài ảnh, video, infographic: ít chữ
    "http_4xx",
    "http_5xx",
    "network_errors",
    "rechecked",
    "changed",             # recheck thấy nội dung đổi
    "removed",             # recheck thấy bài bị gỡ
    "html_bytes",          # tổng kích thước HTML chưa nén
)


class Stats:
    def __init__(self, root: Path):
        self.dir = root / "stats"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._day = None
        self._data: dict[str, Any] = {}
        self._load_today()

    def _path(self, day: str) -> Path:
        return self.dir / f"{day}.json"

    def _load_today(self) -> None:
        day = now().strftime("%Y-%m-%d")
        if day == self._day:
            return
        if self._day is not None:
            self.flush()
        self._day = day
        p = self._path(day)
        if p.exists():
            try:
                self._data = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self._data = {}
        else:
            self._data = {}
        self._data.setdefault("date", day)
        self._data.setdefault("sources", {})
        self._data.setdefault("files", {"count": 0, "gz_bytes": 0, "records": 0})
        self._data.setdefault("gaps", [])

    def _src(self, source: str) -> dict[str, Any]:
        self._load_today()
        s = self._data["sources"].setdefault(source, {})
        for c in COUNTERS:
            s.setdefault(c, 0)
        return s

    def incr(self, source: str, counter: str, n: int = 1) -> None:
        self._src(source)[counter] += n
        if counter == "written":
            self._src(source)["last_written_at"] = now_iso()

    def file_written(self, path: Path, size: int, records: int) -> None:
        self._load_today()
        f = self._data["files"]
        f["count"] += 1
        f["gz_bytes"] += size
        f["records"] += records

    def gap(self, start: str, end: str, source: str | None = None, reason: str = "crawler dừng") -> None:
        self._load_today()
        g = {"from": start, "to": end, "reason": reason}
        if source:
            g["source"] = source
        self._data["gaps"].append(g)

    def last_written_at(self, source: str) -> str | None:
        return self._src(source).get("last_written_at")

    def snapshot(self) -> dict[str, Any]:
        self._load_today()
        return json.loads(json.dumps(self._data))

    def flush(self) -> None:
        if self._day is None:
            return
        self._data["updated_at"] = now_iso()
        p = self._path(self._day)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
