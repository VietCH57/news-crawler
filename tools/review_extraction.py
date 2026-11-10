"""Trích xuất lại HTML đã lưu bằng parser hiện tại và liệt kê bài cần xem lại.

    python tools/review_extraction.py                    # mọi báo, tối đa 5 bài mỗi báo
    python tools/review_extraction.py tienphong nhandan  # chỉ vài báo
    python tools/review_extraction.py --summary          # chỉ đếm theo cách trích xuất

Dùng sau khi sửa parser để xem bài ảnh/video/bố cục lạ được xử lý thế nào.
Không sửa dữ liệu đã lưu (HTML gốc vẫn giữ để vùng clean trích xuất lại).
"""

from __future__ import annotations

import gzip
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crawler.config import load_config  # noqa: E402
from crawler.parsers import get_parser  # noqa: E402


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    summary_only = "--summary" in sys.argv
    cfg = load_config(ROOT / "config.yaml")
    parsers = {n: get_parser(s.parser, cfg.min_words) for n, s in cfg.sources.items()}
    want = set(args) or set(cfg.sources)
    counts: dict[str, Counter] = defaultdict(Counter)
    shown: Counter = Counter()

    for f in sorted((cfg.data_dir / "landing" / "articles").glob("*/*/*.jsonl.gz")):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                s = r["source"]
                if s not in want or not r.get("html"):
                    continue
                p = parsers[s].parse(r["html"], r["url"])
                kind = p.extract_method
                if p.is_short:
                    kind += " (ngắn)" if p.media_type == "text" else f" ({p.media_type})"
                kind += "" if p.parse_ok else " LỖI"
                counts[s][kind] += 1
                if summary_only or shown[s] >= 5 or (p.extract_method == "selector" and p.parse_ok and not p.is_short):
                    continue
                shown[s] += 1
                print(f"[{s}] {kind} | {p.word_count} từ | {r['url']}")
                print(f"    {p.title[:90]}")
                print(f"    {p.content[:150]}")

    print("\nTổng hợp:")
    for s in sorted(counts):
        print(f"  {s:12} " + ", ".join(f"{k}: {v}" for k, v in counts[s].most_common()))


if __name__ == "__main__":
    main()
