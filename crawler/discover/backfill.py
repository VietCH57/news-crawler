"""Liệt kê bài cũ theo ngày, dùng cho crawl ngược và lấp khoảng trống khi máy tắt.

Mỗi báo một cách (khảo sát 8/11/2026):
- VnExpress: sitemap theo ngày bị chuyển hướng về trang chủ, nên dùng trang lưu trữ
  theo ngày của từng chuyên mục.
- Tuổi Trẻ: sitemap tĩnh 5 ngày một file ``/StaticSitemaps/sitemaps-YYYY-M-a-b.xml``.
- Dân trí: sitemap theo tháng ``/sitemaps/Article-YYYY-MM-01.xml``; mã số bài bắt đầu
  bằng ``yyyyMMdd`` nên lọc được theo ngày.

Các báo thêm ngày 10/11/2026 dùng các cách chung, cấu hình bằng ``url_template``:
- ``fiveday_sitemap`` (Thanh Niên): 5 ngày một file, như Tuổi Trẻ.
- ``daily_sitemap`` (VTC News): mỗi ngày một file.
- ``monthly_sitemap_lastmod`` (Nhân Dân, VietnamPlus, Tiền Phong): mỗi tháng một file,
  lọc theo ``<lastmod>``.
- ``index_monthly_lastmod`` (VietnamNet): mỗi tháng nhiều file liệt kê trong sitemap index,
  lọc theo ``<lastmod>``.
Biến trong ``url_template``: {year}, {month} (không đệm số 0), {mm}, {day}, {dd}, {a}, {b}.
"""

from __future__ import annotations

import calendar
import logging
import re
from collections import OrderedDict
from datetime import date, datetime, time, timedelta
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from ..config import SourceConfig
from ..fetcher import Fetcher
from ..normalize import extract_native_id, normalize_url
from ..timeutil import VN_TZ

log = logging.getLogger(__name__)

_LOC = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*([^<\]\s]+)\s*(?:\]\]>)?\s*</loc>", re.I)
_URL_BLOCK = re.compile(r"<url>(.*?)</url>", re.I | re.S)
_LASTMOD = re.compile(r"<(?:lastmod|news:publication_date)>\s*([^<\s]+)", re.I)


class _Cache:
    """Giữ vài sitemap đã tải để không tải lại khi duyệt từng ngày trong cùng file."""

    def __init__(self, size: int = 8):
        self.size = size
        self.data: OrderedDict[str, list[str]] = OrderedDict()

    def get(self, key: str) -> list[str] | None:
        if key in self.data:
            self.data.move_to_end(key)
            return self.data[key]
        return None

    def put(self, key: str, value: list[str]) -> None:
        self.data[key] = value
        self.data.move_to_end(key)
        while len(self.data) > self.size:
            self.data.popitem(last=False)


_cache = _Cache()


def parse_sitemap_locs(xml: str) -> list[str]:
    return [m.group(1).replace("&amp;", "&") for m in _LOC.finditer(xml)]


def parse_sitemap_entries(xml: str) -> list[tuple[str, str | None]]:
    """[(url, lastmod)] từ một sitemap <urlset>."""
    out = []
    for block in _URL_BLOCK.findall(xml):
        m = _LOC.search(block)
        if not m:
            continue
        lm = _LASTMOD.search(block)
        out.append((m.group(1).replace("&amp;", "&"), lm.group(1) if lm else None))
    return out


async def _sitemap_entries(fetcher: Fetcher, url: str) -> list[tuple[str, str | None]]:
    cached = _cache.get(url)
    if cached is not None:
        return cached
    res = await fetcher.fetch(url, backfill=True)
    if not res.ok:
        raise RuntimeError(f"Không tải được {url}: HTTP {res.status} {res.error or ''}")
    entries = parse_sitemap_entries(res.text)
    if not entries:  # sitemap index hoặc định dạng lạ: chỉ lấy <loc>
        entries = [(u, None) for u in parse_sitemap_locs(res.text)]
    _cache.put(url, entries)
    return entries


async def _sitemap(fetcher: Fetcher, url: str) -> list[str]:
    return [u for u, _ in await _sitemap_entries(fetcher, url)]


def fill_template(template: str, day: date) -> str:
    last = calendar.monthrange(day.year, day.month)[1]
    a = max(s for s in (1, 6, 11, 16, 21, 26) if s <= day.day)
    b = last if a == 26 else a + 4
    return template.format(
        year=day.year, month=day.month, mm=f"{day.month:02d}", day=day.day, dd=f"{day.day:02d}", a=a, b=b
    )


def _on_day(lastmod: str | None, day: date) -> bool:
    return bool(lastmod) and lastmod[:10] == day.isoformat()


# ---------- Tuổi Trẻ ----------
def tuoitre_sitemap_url(day: date) -> str:
    last = calendar.monthrange(day.year, day.month)[1]
    starts = [1, 6, 11, 16, 21, 26]
    a = max(s for s in starts if s <= day.day)
    b = last if a == 26 else a + 4
    return f"https://tuoitre.vn/StaticSitemaps/sitemaps-{day.year}-{day.month}-{a}-{b}.xml"


async def list_tuoitre(fetcher: Fetcher, src: SourceConfig, day: date) -> list[str]:
    # File phủ 5 ngày; trả về cả file, phần trùng được loại khi đưa vào hàng đợi.
    return await _sitemap(fetcher, tuoitre_sitemap_url(day))


# ---------- Dân trí ----------
def dantri_sitemap_url(day: date) -> str:
    return f"https://dantri.com.vn/sitemaps/Article-{day.year}-{day.month:02d}-01.xml"


async def list_dantri(fetcher: Fetcher, src: SourceConfig, day: date) -> list[str]:
    locs = await _sitemap(fetcher, dantri_sitemap_url(day))
    prefix = day.strftime("%Y%m%d")
    out = []
    for u in locs:
        nid = extract_native_id(u, src.id_pattern)
        if nid and nid.startswith(prefix):
            out.append(u)
    return out


# ---------- VnExpress ----------
def vnexpress_day_url(cate_id: int, day: date) -> str:
    start = int(datetime.combine(day, time(0, 0), tzinfo=VN_TZ).timestamp())
    end = start + 86399
    return (
        f"https://vnexpress.net/category/day/cateid/{cate_id}"
        f"/fromdate/{start}/todate/{end}/allcate/{cate_id}"
    )


def _vnexpress_links(html: str, base: str, id_pattern: str) -> tuple[list[str], str | None]:
    tree = LexborHTMLParser(html)
    links: list[str] = []
    for a in tree.css("h3.title-news a, h2.title-news a, h4.title-news a"):
        href = a.attributes.get("href")
        if href:
            url = urljoin(base, href)
            if extract_native_id(url, id_pattern) and url not in links:
                links.append(url)
    nxt = None
    for sel in ('a[rel="next"]', "a.next-page", "a.btn-page.next-page"):
        node = tree.css_first(sel)
        if node is not None and node.attributes.get("href"):
            nxt = urljoin(base, node.attributes["href"])
            break
    return links, nxt


async def list_vnexpress(fetcher: Fetcher, src: SourceConfig, day: date, max_pages: int = 30) -> list[str]:
    out: list[str] = []
    for cate in src.backfill.category_ids:
        url: str | None = vnexpress_day_url(cate, day)
        visited: set[str] = set()
        for _ in range(max_pages):
            if url is None or url in visited:
                break
            visited.add(url)
            res = await fetcher.fetch(url, backfill=True)
            if not res.ok:
                log.warning("[vnexpress] Không tải được trang lưu trữ %s: %s", url, res.status)
                break
            if normalize_url(res.final_url).rstrip("/") == "https://vnexpress.net":
                log.warning("[vnexpress] Trang lưu trữ chuyên mục %s bị chuyển về trang chủ", cate)
                break
            links, url = _vnexpress_links(res.text, res.final_url, src.id_pattern)
            new = [u for u in links if u not in out]
            out.extend(new)
            if not new:
                break
    return out


# ---------- Cách chung theo url_template ----------
def _template(src: SourceConfig) -> str:
    if not src.backfill.url_template:
        raise RuntimeError(f"[{src.name}] Thiếu backfill.url_template trong config.yaml")
    return src.backfill.url_template


async def list_fiveday(fetcher: Fetcher, src: SourceConfig, day: date) -> list[str]:
    return await _sitemap(fetcher, fill_template(_template(src), day))


async def list_daily(fetcher: Fetcher, src: SourceConfig, day: date) -> list[str]:
    return await _sitemap(fetcher, fill_template(_template(src), day))


async def list_monthly_lastmod(fetcher: Fetcher, src: SourceConfig, day: date) -> list[str]:
    entries = await _sitemap_entries(fetcher, fill_template(_template(src), day))
    return [u for u, lm in entries if _on_day(lm, day)]


async def list_index_monthly_lastmod(fetcher: Fetcher, src: SourceConfig, day: date) -> list[str]:
    """VietnamNet: sitemap index liệt kê sitemap-article-MM-YYYY-NN.xml; lấy các file của tháng."""
    if not src.backfill.index_url:
        raise RuntimeError(f"[{src.name}] Thiếu backfill.index_url trong config.yaml")
    prefix = fill_template(_template(src), day)
    files = [u for u in await _sitemap(fetcher, src.backfill.index_url) if u.startswith(prefix)]
    out: list[str] = []
    for f in sorted(files):
        out.extend(u for u, lm in await _sitemap_entries(fetcher, f) if _on_day(lm, day))
    return out


LISTERS = {
    "vnexpress_day_archive": list_vnexpress,
    "tuoitre_static_sitemap": list_tuoitre,
    "dantri_monthly_sitemap": list_dantri,
    "fiveday_sitemap": list_fiveday,
    "daily_sitemap": list_daily,
    "monthly_sitemap_lastmod": list_monthly_lastmod,
    "index_monthly_lastmod": list_index_monthly_lastmod,
}


async def list_day(fetcher: Fetcher, src: SourceConfig, day: date) -> list[str]:
    lister = LISTERS.get(src.backfill.method or "")
    if lister is None:
        return []
    return await lister(fetcher, src, day)


def days_between(start: date, end: date) -> list[date]:
    """Các ngày từ start đến end (cả hai đầu), mới nhất trước."""
    n = (end - start).days
    return [end - timedelta(days=i) for i in range(n + 1)]
