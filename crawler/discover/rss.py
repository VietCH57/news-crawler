"""Đọc RSS (hoặc Google News sitemap) để phát hiện bài mới."""

from __future__ import annotations

import logging
import re
from datetime import timedelta

import feedparser

from ..config import SourceConfig
from ..fetcher import Fetcher
from ..normalize import make_article_id, normalize_url
from ..state import State
from ..stats import Stats
from ..timeutil import from_iso, now, parse_date

log = logging.getLogger(__name__)

_NEWS_URL = re.compile(r"<url>(.*?)</url>", re.I | re.S)
_NEWS_LOC = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*([^<\]\s]+)", re.I)
_NEWS_DATE = re.compile(r"<news:publication_date>\s*([^<\s]+)", re.I)
_NEWS_TITLE = re.compile(r"<news:title>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</news:title>", re.I | re.S)


def parse_news_sitemap(text: str) -> list[dict]:
    """Google News sitemap (<urlset> + <news:news>): dùng thay RSS khi RSS quá nặng
    (RSS của VietnamNet chứa 1.000 bài, khoảng 1 MB)."""
    items = []
    for block in _NEWS_URL.findall(text):
        loc = _NEWS_LOC.search(block)
        if not loc:
            continue
        d = _NEWS_DATE.search(block)
        t = _NEWS_TITLE.search(block)
        items.append(
            {
                "url": loc.group(1).replace("&amp;", "&"),
                "title": (t.group(1).strip() if t else ""),
                "published": d.group(1) if d else None,
                "category": None,
            }
        )
    return items


def parse_feed(text: str) -> list[dict]:
    """Trả về danh sách {url, title, published, category} từ RSS/Atom hoặc News sitemap."""
    if "<urlset" in text[:3000]:
        return parse_news_sitemap(text)
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
    src: SourceConfig,
    fetcher: Fetcher,
    state: State,
    stats: Stats,
    feeds: list[str] | None = None,
    max_age_hours: float | None = None,
) -> int:
    """Đọc các feed của một báo, đưa URL chưa thấy vào hàng đợi. Trả về số URL mới.

    ``max_age_hours``: bỏ qua bài đăng cũ hơn mốc này. Dùng cho lần chạy đầu của một
    báo mới, để không tải dồn hàng trăm bài một lúc (bài học từ lần Dân trí chặn IP).
    """
    new = 0
    skipped_old = 0
    cutoff = now() - timedelta(hours=max_age_hours) if max_age_hours else None
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
            url = normalize_url(it["url"], src.host)
            if fetcher.domain_of(url) != src.domain:
                continue  # bài trỏ sang trang khác (quảng cáo, chuyên trang)
            if cutoff is not None:
                pub = parse_date(it["published"])
                if pub and from_iso(pub) < cutoff:
                    skipped_old += 1
                    continue
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
    if skipped_old:
        log.info("[%s] Khởi động nhẹ: bỏ qua %d mục cũ hơn %.0f giờ", src.name, skipped_old, max_age_hours)
    state.set(f"rss_ok:{src.name}", True)
    return new
