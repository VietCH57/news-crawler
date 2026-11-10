"""Đọc RSS để phát hiện bài mới."""

from __future__ import annotations

import logging

import feedparser

from ..config import SourceConfig
from ..fetcher import Fetcher
from ..normalize import make_article_id, normalize_url
from ..state import State
from ..stats import Stats

log = logging.getLogger(__name__)


def parse_feed(text: str) -> list[dict]:
    """Trả về danh sách {url, title, published, category} từ nội dung RSS."""
    feed = feedparser.parse(text)
    items = []
    for e in feed.entries:
        link = (e.get("link") or e.get("id") or "").strip()
        if not link.startswith("http"):
            continue
        cat = None
        if e.get("tags"):
            cat = e.tags[0].get("term")
        items.append(
            {
                "url": link,
                "title": (e.get("title") or "").strip(),
                "published": e.get("published") or e.get("updated"),
                "category": cat,
            }
        )
    return items


async def poll_source(
    src: SourceConfig, fetcher: Fetcher, state: State, stats: Stats, feeds: list[str] | None = None
) -> int:
    """Đọc các feed của một báo, đưa URL chưa thấy vào hàng đợi. Trả về số URL mới."""
    new = 0
    for feed_url in feeds if feeds is not None else src.feeds:
        if fetcher.paused_for(src.domain) > 0:
            break
        res = await fetcher.fetch(feed_url)
        if not res.ok:
            log.warning("[%s] Không đọc được feed %s: %s %s", src.name, feed_url, res.status, res.error or "")
            continue
        items = parse_feed(res.text)
        stats.incr(src.name, "feed_items", len(items))
        for it in items:
            url = normalize_url(it["url"])
            if fetcher.domain_of(url) != src.domain:
                continue  # bài trỏ sang trang khác (quảng cáo, chuyên trang)
            aid = make_article_id(src.name, url, src.id_pattern)
            if state.seen(aid):
                continue
            hint = {"rss_title": it["title"], "rss_published": it["published"], "feed": feed_url}
            if it["category"]:
                hint["rss_category"] = it["category"]
            if state.enqueue(aid, url, src.name, "rss", hint):
                new += 1
    if new:
        stats.incr(src.name, "discovered", new)
        log.info("[%s] %d bài mới từ RSS", src.name, new)
    state.set(f"rss_ok:{src.name}", True)
    return new
