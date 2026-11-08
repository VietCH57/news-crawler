from datetime import date

from conftest import fixture_text

from crawler.discover.backfill import (
    _vnexpress_links,
    dantri_sitemap_url,
    days_between,
    parse_sitemap_locs,
    tuoitre_sitemap_url,
    vnexpress_day_url,
)


def test_tuoitre_sitemap_ranges_match_real_names():
    # Tên file thật đã thấy: sitemaps-2026-11-6-10.xml, sitemaps-2026-5-6-10.xml, sitemaps-2011-1-26-31.xml
    assert tuoitre_sitemap_url(date(2026, 11, 8)).endswith("/sitemaps-2026-11-6-10.xml")
    assert tuoitre_sitemap_url(date(2026, 5, 6)).endswith("/sitemaps-2026-5-6-10.xml")
    assert tuoitre_sitemap_url(date(2011, 1, 31)).endswith("/sitemaps-2011-1-26-31.xml")
    assert tuoitre_sitemap_url(date(2026, 2, 27)).endswith("/sitemaps-2026-2-26-28.xml")
    assert tuoitre_sitemap_url(date(2026, 3, 1)).endswith("/sitemaps-2026-3-1-5.xml")


def test_dantri_sitemap_name():
    assert dantri_sitemap_url(date(2026, 5, 17)) == "https://dantri.com.vn/sitemaps/Article-2026-05-01.xml"


def test_vnexpress_day_url_matches_real_timestamps():
    # URL đã thử thành công trong trình duyệt cho ngày 8/5/2026, chuyên mục Thời sự.
    assert vnexpress_day_url(1001005, date(2026, 5, 8)) == (
        "https://vnexpress.net/category/day/cateid/1001005/fromdate/1778173200/todate/1778259599/allcate/1001005"
    )


def test_vnexpress_archive_links_and_next_page():
    links, nxt = _vnexpress_links(fixture_text("vnexpress_archive.html"), "https://vnexpress.net/category/day/x", r"-(\d+)\.html$")
    assert links == [
        "https://vnexpress.net/thu-tuong-yeu-cau-dieu-dong-5071694.html",
        "https://vnexpress.net/xe-chay-tren-cau-binh-loi-5071660.html",
    ]
    assert nxt.endswith("/page/2")


def test_parse_sitemap_locs_handles_cdata_and_entities():
    xml = "<urlset><url><loc><![CDATA[https://a.vn/x-1.htm]]></loc></url><url><loc>https://a.vn/y.xml?m=1&amp;d=2</loc></url></urlset>"
    assert parse_sitemap_locs(xml) == ["https://a.vn/x-1.htm", "https://a.vn/y.xml?m=1&d=2"]


def test_days_between_newest_first():
    assert days_between(date(2026, 11, 6), date(2026, 11, 8)) == [date(2026, 11, 8), date(2026, 11, 7), date(2026, 11, 6)]
