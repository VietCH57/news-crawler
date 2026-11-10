"""Dòng lệnh.

    python -m crawler run                 chạy liên tục (Ctrl+C để dừng)
    python -m crawler once --limit 5      đọc RSS một lần, tải tối đa 5 bài mỗi báo rồi thoát
    python -m crawler parse <url> [--source vnexpress]   thử trích xuất một bài, không ghi file
    python -m crawler parse <file.html> --source dantri  thử trên file HTML đã lưu
    python -m crawler stats [--date 2026-11-09]          xem thống kê
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from .config import load_config


def setup_logging(log_dir: Path, verbose: bool) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = TimedRotatingFileHandler(log_dir / "crawler.log", when="midnight", backupCount=30, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("trafilatura").setLevel(logging.WARNING)


def _utf8_stdout() -> None:
    # Console Windows mặc định không phải UTF-8; tránh lỗi khi in tiếng Việt.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass


async def _run(cfg) -> None:
    from .app import Crawler

    crawler = Crawler(cfg)
    try:
        await crawler.run()
    finally:
        await crawler.close()


async def _once(cfg, limit: int) -> None:
    from .app import Crawler

    crawler = Crawler(cfg)
    try:
        done = await crawler.once(limit)
        print("Đã tải:", json.dumps(done, ensure_ascii=False))
    finally:
        await crawler.close()
    snap = crawler.stats.snapshot()
    print(json.dumps(snap["sources"], ensure_ascii=False, indent=2))


async def _parse(cfg, target: str, source: str | None) -> None:
    from .fetcher import Fetcher
    from .normalize import make_article_id, normalize_url
    from .parsers import get_parser

    src = cfg.sources.get(source) if source else None
    if src is None and target.startswith("http"):
        domain = Fetcher.domain_of(target)
        src = next((s for s in cfg.sources.values() if s.domain == domain), None)
    if src is None:
        raise SystemExit("Không xác định được báo; thêm --source <tên>")

    if target.startswith("http"):
        f = Fetcher(cfg.user_agent, cfg.request_timeout, {src.domain: src.rate_limit}, {})
        try:
            res = await f.fetch(target)
        finally:
            await f.close()
        if not res.ok:
            raise SystemExit(f"HTTP {res.status} {res.error or ''}")
        html, url = res.text, normalize_url(res.final_url)
    else:
        html, url = Path(target).read_text(encoding="utf-8"), target

    art = get_parser(src.parser, cfg.min_words).parse(html, url)
    out = {
        "article_id": make_article_id(src.name, url, src.id_pattern) if url.startswith("http") else None,
        "url": url,
        "parse_ok": art.parse_ok,
        "extract_method": art.extract_method,
        "is_short": art.is_short,
        "media_type": art.media_type,
        "word_count": art.word_count,
        "title": art.title,
        "sapo": art.sapo,
        "author": art.author,
        "category_raw": art.category_raw,
        "tags": art.tags,
        "published_at": art.published_at,
        "html_kb": round(len(html.encode("utf-8")) / 1024, 1),
        "content_preview": art.content[:600],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


def _stats(cfg, day: str | None) -> None:
    from .timeutil import now

    day = day or now().strftime("%Y-%m-%d")
    p = cfg.data_dir / "stats" / f"{day}.json"
    if not p.exists():
        raise SystemExit(f"Chưa có thống kê cho ngày {day}")
    print(p.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> None:
    _utf8_stdout()
    ap = argparse.ArgumentParser(prog="python -m crawler", description="Crawler báo điện tử Việt Nam")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="chạy liên tục")
    p_once = sub.add_parser("once", help="đọc RSS một lần và tải vài bài để thử")
    p_once.add_argument("--limit", type=int, default=5)
    p_parse = sub.add_parser("parse", help="thử trích xuất một bài")
    p_parse.add_argument("target", help="URL hoặc đường dẫn file HTML")
    p_parse.add_argument("--source")
    p_stats = sub.add_parser("stats", help="xem thống kê một ngày")
    p_stats.add_argument("--date")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)

    if args.cmd == "stats":
        _stats(cfg, args.date)
        return
    if args.cmd == "parse":
        logging.basicConfig(level=logging.WARNING)
        asyncio.run(_parse(cfg, args.target, args.source))
        return

    setup_logging(cfg.data_dir / "logs", args.verbose)
    try:
        if args.cmd == "run":
            asyncio.run(_run(cfg))
        elif args.cmd == "once":
            asyncio.run(_once(cfg, args.limit))
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Đã dừng.")


if __name__ == "__main__":
    main()
