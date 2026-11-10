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
            n = await poll_source(src, f, st, Stats(tmp_path), [src.feeds[0]], since=to_iso(now() - timedelta(hours=3)))
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


def test_vov_parser():
    a = get_parser("vov").parse(fixture_text("vov_article.html"), "https://vov.vn/phap-luat/x-post1348841.vov")
    assert a.parse_ok
    assert a.author == "Trọng Phú"  # bỏ "/VOV.VN"
    assert a.sapo.startswith("Ngày 10/11")  # bỏ "VOV.VN - "
    assert "Ảnh:" not in a.content


def test_sggp_parser_author_from_jsonld_and_plus0700():
    a = get_parser("sggp").parse(fixture_text("sggp_article.html"), "https://www.sggp.org.vn/x-post880993.html")
    assert a.parse_ok
    assert a.author == "ĐỖ HOÀNG"  # không lấy "SGGPO" trên trang
    assert a.published_at == "2026-11-10T13:14:10+07:00"


def test_parse_date_plus0700():
    from crawler.timeutil import parse_date

    assert parse_date("2026-11-10T13:14:10+0700") == "2026-11-10T13:14:10+07:00"
    assert parse_date("2026-11-10T13:14:10-0130") == "2026-11-10T21:44:10+07:00"


def test_crawl_delay_and_subdomain_share_limits(cfg):
    from crawler.fetcher import Fetcher

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nCrawl-delay: 20\nAllow: /\n")
        return httpx.Response(200, text="ok")

    async def go():
        f = Fetcher(cfg.user_agent, 5, {"vov.vn": 1.0, "sggp.org.vn": 0.5}, {"vov.vn": 1.0}, transport=httpx.MockTransport(handler))
        try:
            await f.allowed("https://vov.vn/x-post1.vov")
            return (
                f._limiter("vov.vn").base_rate,
                f.backfill_limiters["vov.vn"].base_rate,
                f._limiter("thethao.sggp.org.vn") is f._limiter("sggp.org.vn"),
                f.health("thethao.sggp.org.vn") is f.health("sggp.org.vn"),
            )
        finally:
            await f.close()

    rate, brate, same_lim, same_health = asyncio.run(go())
    assert rate == brate == 1 / 20
    assert same_lim and same_health


_RELATED = "".join(
    f"<p>Tin liên quan số {i}: một bài khác hoàn toàn về chủ đề khác, có rất nhiều chữ để làm nhiễu.</p>" for i in range(60)
)


def _zce_media_page(body_inner: str) -> str:
    return (
        '<html><head><meta property="og:title" content="Bài ảnh Tổng Bí thư tiếp khách"/>'
        '<meta property="article:published_time" content="2026-11-10T11:00:00+07:00"/></head>'
        '<body><div class="site-body"><div class="container"><div class="article">'
        '<div class="article__sapo cms-desc">Sáng 10/11, tại Tokyo, Tổng Bí thư tiếp các nguyên Thủ tướng.</div>'
        f'<div class="article__body zce-content-body">{body_inner}</div></div>'
        f'<div class="timeline secondary">{_RELATED}</div></div></div></body></html>'
    )


def test_photo_story_uses_captions_not_related_news():
    # Mô phỏng bài ảnh VietnamPlus post1147335: thân bài chỉ có ảnh + chú thích.
    body = "".join(
        f"<figure><img src='a{i}.jpg'/><figcaption>Chú thích ảnh {i}: Tổng Bí thư bắt tay khách tại buổi tiếp.</figcaption></figure>"
        for i in range(4)
    )
    a = get_parser("vietnamplus").parse(_zce_media_page(body), "https://www.vietnamplus.vn/x-post1147335.vnp")
    assert a.extract_method == "selector+captions"
    assert "Chú thích ảnh 0" in a.content and "Tin liên quan" not in a.content
    assert a.parse_ok


def test_infographic_short_but_ok_without_trafilatura():
    # Mô phỏng infographic VietnamPlus post1147358: 1 đoạn chữ ngắn, bên cạnh là dòng tin liên quan.
    body = "<figure><img src='info.jpg'/></figure><p>Infographic: 5 lưu ý giúp bảo vệ sức khỏe khi trời trở lạnh.</p>"
    a = get_parser("vietnamplus").parse(_zce_media_page(body), "https://www.vietnamplus.vn/x-post1147358.vnp")
    assert a.extract_method == "selector"
    assert a.is_short and a.parse_ok
    assert "Tin liên quan" not in a.content


def test_unknown_layout_still_falls_back_to_trafilatura():
    html = "<html><head><title>Bố cục lạ</title><meta property='og:title' content='Bố cục lạ'/></head><body><main><article>" + "".join(
        f"<p>Đoạn {i}: đây là một đoạn văn dài của bài báo có bố cục hoàn toàn mới mà parser chưa biết tới, cần trích xuất dự phòng.</p>" for i in range(8)
    ) + "</article></main></body></html>"
    a = get_parser("vietnamplus").parse(html, "https://www.vietnamplus.vn/x-post1.vnp")
    assert a.extract_method == "trafilatura" and a.parse_ok


def test_warm_start_cutoff_persists(cfg):
    from crawler.app import Crawler

    c = Crawler(cfg, transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    try:
        cfg.warm_start_hours = 3
        first = c._since(c.sources["vov"])
        assert first is not None
        # Sau khi đã có bài, mốc vẫn giữ nguyên (bản cũ mất tác dụng ngay lần đọc thứ hai).
        c.state.add_article("x", "vov", "u", "h", to_iso(now()), None)
        assert c._since(c.sources["vov"]) == first
        # Báo đã chạy từ trước (có bài, chưa có mốc): không lọc.
        c.state.add_article("y", "vnexpress", "u2", "h", to_iso(now()), None)
        assert c._since(c.sources["vnexpress"]) is None
    finally:
        asyncio.run(c.close())
