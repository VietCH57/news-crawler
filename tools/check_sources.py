"""Thử nhanh một hoặc nhiều báo: đọc feed, tải bài đầu tiên, in kết quả trích xuất.

    python tools/check_sources.py sggp vov
    python tools/check_sources.py            # mọi báo đang bật

Không ghi gì vào data/. Tuân theo robots.txt và Crawl-delay như crawler.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crawler.config import load_config  # noqa: E402
from crawler.discover.rss import parse_feed  # noqa: E402
from crawler.fetcher import Fetcher  # noqa: E402
from crawler.parsers import get_parser  # noqa: E402


async def check(cfg, name: str) -> None:
    s = cfg.sources[name]
    f = Fetcher(cfg.user_agent, 30, {s.domain: s.rate_limit}, {})
    try:
        r = await f.fetch(s.feeds[0])
        items = parse_feed(r.text) if r.ok else []
        print(f"\n[{name}] feed: HTTP {r.status} {r.error or ''} | {len(items)} mục | {len(r.text)} byte")
        if not items:
            return
        a = await f.fetch(items[0]["url"])
        print(f"  bài: HTTP {a.status} {a.error or ''} {a.final_url}")
        if not a.ok:
            return
        p = get_parser(s.parser, cfg.min_words).parse(a.text, a.final_url)
        print(
            f"  parse_ok={p.parse_ok} extract={p.extract_method} short={p.is_short} media={p.media_type} {p.word_count} từ"
            f" | {p.published_at} | {p.author} | {p.category_raw}"
        )
        print(f"  tiêu đề: {p.title}")
        print(f"  sapo: {p.sapo[:120]}")
        print(f"  đầu: {p.content[:120]}")
        print(f"  cuối: {p.content[-120:]}")
    finally:
        await f.close()


async def main() -> None:
    cfg = load_config(ROOT / "config.yaml")
    names = sys.argv[1:] or [s.name for s in cfg.enabled_sources]
    for n in names:
        if n not in cfg.sources:
            print(f"Không có báo '{n}'. Các báo: {', '.join(cfg.sources)}")
            continue
        await check(cfg, n)


if __name__ == "__main__":
    asyncio.run(main())
