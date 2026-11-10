"""Các báo thêm ngày 10/11/2026: trang mẫu mô phỏng cấu trúc đã khảo sát cùng ngày."""

import asyncio
from datetime import date, timedelta

import httpx
from conftest import fixture_text

from crawler.discover.backfill import (
    _cache,
    fill_template,
    list_index_monthly_lastmod,
    list_monthly_lastmod,
    parse_sitemap_entries,
)
from crawler.discover.rss import parse_feed, poll_source
from crawler.fetcher import Fetcher
from crawler.normalize import make_article_id, normalize_url
from crawler.parsers import get_parser
from crawler.state import State
from crawler.stats import Stats
from crawler.timeutil import now, to_iso


def test_zce_parser_tienphong():
    a = get_parser("tienphong").parse(fixture_text("tienphong_article.html"), "https://tienphong.vn/x-post1893070.tpo")
    assert a.parse_ok and a.author == "Lưu Trinh"
    assert a.sapo.startswith("Thương mại hóa")  # đã bỏ "TPO - "
    assert a.published_at == "2026-11-10T12:55:07+07:00"
    assert "Ảnh minh họa" not in a.content


def test_vietnamnet_parser_uses_jsonld_author():
    a = get_parser("vietnamnet").parse(fixture_text("vietnamnet_article.html"), "https://vietnamnet.vn/x-2573351.html")
    assert a.parse_ok
    assert a.author == "Hồ Giáp"  # không lẫn "Xem các bài viết của tác giả"
    assert a.published_at == "2026-11-10T12:53:00+07:00"
    assert a.category_raw == "Dân sinh"
    assert "Chú thích ảnh" not in a.content


def test_vtc_parser_sapo_from_jsonld():
    a = get_parser("vtcnews").parse(fixture_text("vtcnews_article.html"), "https://vtcnews.vn/x-ar1050353.html")
    assert a.parse_ok and a.author == "Trần Hạnh Lê"
    assert a.sapo == "Sapo VTC lấy từ JSON-LD."
    assert "adsbygoogle" not in a.content


def test_laodong_and_thanhnien_parsers():
    a = get_parser("laodong").parse(fixture_text("laodong_article.html"), "https://laodong.vn/a/x-1797763.ldo")
    assert a.parse_ok and a.author == "Quang Minh" and a.category_raw == "Dinh dưỡng"
    b = get_parser("thanhnien").parse(fixture_text("thanhnien_article.html"), "https://thanhnien.vn/x-185261110124506387.htm")
    assert b.parse_ok and b.author == "Thanh Chi" and b.sapo.startswith("Brandon")


def test_native_ids_of_new_sources():
    assert make_article_id("nhandan", "https://nhandan.vn/a-b-post1001463.html", r"post(\d+)\.html$") == make_article_id(
        "nhandan", "https://nhandan.vn/tieu-de-moi-post1001463.html", r"post(\d+)\.html$"
    )
    from crawler.normalize import extract_native_id

    assert extract_native_id("https://www.vietnamplus.vn/x-post1147372.vnp", r"post(\d+)\.vnp$") == "1147372"
    assert extract_native_id("https://tienphong.vn/x-post1893070.tpo", r"post(\d+)\.tpo$") == "1893070"
    assert extract_native_id("https://vtcnews.vn/x-ar1050353.html", r"ar(\d+)\.html$") == "1050353"
    assert extract_native_id("https://laodong.vn/a/x-1797763.ldo", r"-(\d+)\.ldo$") == "1797763"


def test_host_keeps_www_for_vietnamplus():
    assert normalize_url("https://vietnamplus.vn/x-post1.vnp", "www.vietnamplus.vn") == "https://www.vietnamplus.vn/x-post1.vnp"
    assert normalize_url("https://www.vietnamplus.vn/x-post1.vnp?utm_source=a", "www.vietnamplus.vn") == "https://www.vietnamplus.vn/x-post1.vnp"
    # báo khác không bị ảnh hưởng
    assert normalize_url("https://www.vnexpress.net/x-1.html") == "https://vnexpress.net/x-1.html"


def test_news_sitemap_as_feed():
    items = parse_feed(fixture_text("vietnamnet_news_sitemap.xml"))
    assert [i["url"] for i in items] == [
        "https://vietnamnet.vn/hue-tau-hang-mac-can-2573351.html",
        "https://vietnamnet.vn/bai-cu-2571000.html",
    ]
    assert items[0]["title"] == "Tàu hàng mắc cạn" and items[1]["title"] == "Bài cũ"
    assert items[0]["published"].startswith("2026-11-10T12:53")


def test_laodong_long_day_name_date():
    xml = "<rss><channel><item><title>x</title><link>https://laodong.vn/a/x-1.ldo</link><pubDate>Tuesday, 10 November 2026 13:00:00 +07:00</pubDate></item></channel></rss>"
    from crawler.timeutil import parse_date

    assert parse_date(parse_feed(xml)[0]["published"]) == "2026-11-10T13:00:00+07:00"


def test_warm_start_skips_old_items(cfg, tmp_path):
    src = cfg.sources["vtcnews"]
    recent = to_iso(now() - timedelta(hours=1))
    old = to_iso(now() - timedelta(hours=10))
    rss = (
        "<rss><channel>"
        f"<item><title>moi</title><link>https://vtcnews.vn/moi-ar1.html</link><pubDate>{recent}</pubDate></item>"
        f"<item><title>cu</title><link>https://vtcnews.vn/cu-ar2.html</link><pubDate>{old}</pubDate></item>"
        "</channel></rss>"
    )

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, text=rss)

    async def go():
        f = Fetcher(cfg.user_agent, 5, {src.domain: 1000}, {}, transport=httpx.MockTransport(handler))
        st = State(tmp_path / "s.db")
        try:
            n = await poll_source(src, f, st, Stats(tmp_path), [src.feeds[0]], max_age_hours=3)
            return n, st.pending_count("vtcnews")
        finally:
            await f.close()

    assert asyncio.run(go()) == (1, 1)


def test_fill_template():
    d = date(2026, 5, 8)
    assert fill_template("https://nhandan.vn/sitemaps/news-{year}-{month}.xml", d).endswith("news-2026-5.xml")
    assert fill_template("https://vtcnews.vn/sitemap-article-{year}-{mm}-{dd}.xml", d).endswith("2026-05-08.xml")
    assert fill_template("https://thanhnien.vn/sitemaps/sitemaps-{year}-{month}-{a}-{b}.xml", d).endswith("sitemaps-2026-5-6-10.xml")
    assert fill_template("https://vietnamnet.vn/sitemap-article-{mm}-{year}-", date(2026, 11, 3)) == "https://vietnamnet.vn/sitemap-article-11-2026-"


def _sm(*rows):
    return "<urlset>" + "".join(f"<url><loc>{u}</loc><lastmod>{lm}</lastmod></url>" for u, lm in rows) + "</urlset>"


def test_monthly_and_index_listers_filter_by_lastmod(cfg):
    _cache.data.clear()
    pages = {
        "/sitemaps/news-2026-5.xml": _sm(
            ("https://tienphong.vn/a-post1.tpo", "2026-05-08T10:00:00+07:00"),
            ("https://tienphong.vn/b-post2.tpo", "2026-05-09T10:00:00+07:00"),
        ),
        "/sitemap.xml": "<sitemapindex><sitemap><loc>https://vietnamnet.vn/sitemap-article-05-2026-01.xml</loc></sitemap>"
        "<sitemap><loc>https://vietnamnet.vn/sitemap-article-05-2026-02.xml</loc></sitemap>"
        "<sitemap><loc>https://vietnamnet.vn/sitemap-article-04-2026-01.xml</loc></sitemap></sitemapindex>",
        "/sitemap-article-05-2026-01.xml": _sm(("https://vietnamnet.vn/x-1.html", "2026-05-08T09:00:00.000+07:00")),
        "/sitemap-article-05-2026-02.xml": _sm(("https://vietnamnet.vn/y-2.html", "2026-05-07T09:00:00.000+07:00")),
    }

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        body = pages.get(request.url.path)
        return httpx.Response(200 if body else 404, text=body or "")

    async def go():
        f = Fetcher(cfg.user_agent, 5, {"tienphong.vn": 1000, "vietnamnet.vn": 1000},
                    {"tienphong.vn": 1000, "vietnamnet.vn": 1000}, transport=httpx.MockTransport(handler))
        try:
            tp = await list_monthly_lastmod(f, cfg.sources["tienphong"], date(2026, 5, 8))
            vnn = await list_index_monthly_lastmod(f, cfg.sources["vietnamnet"], date(2026, 5, 8))
            return tp, vnn
        finally:
            await f.close()

    tp, vnn = asyncio.run(go())
    assert tp == ["https://tienphong.vn/a-post1.tpo"]
    assert vnn == ["https://vietnamnet.vn/x-1.html"]


def test_parse_sitemap_entries():
    xml = _sm(("https://a.vn/x-1.htm", "2026-05-08T10:00:00+07:00"))
    assert parse_sitemap_entries(xml) == [("https://a.vn/x-1.htm", "2026-05-08T10:00:00+07:00")]
