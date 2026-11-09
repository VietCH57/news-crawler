"""Test parser trên trang mẫu mô phỏng cấu trúc thật (khảo sát 8/11/2026).

Trang thật không được đưa vào repo vì bản quyền. Để kiểm tra trên trang thật:
``python tools/save_fixture.py <url>`` rồi ``python -m crawler parse tests/fixtures/real/<file> --source <báo>``.
"""

from conftest import fixture_text

from crawler.parsers import get_parser


def test_vnexpress():
    a = get_parser("vnexpress").parse(
        fixture_text("vnexpress_article.html"),
        "https://vnexpress.net/lua-bao-trum-quan-karaoke-co-nhieu-khach-o-dong-nai-5142197.html",
    )
    assert a.parse_ok
    assert a.title == "Lửa bao trùm quán karaoke có nhiều khách ở Đồng Nai"
    assert a.sapo.startswith("Đồng Nai")
    assert a.published_at == "2026-11-08T18:21:11+07:00"
    assert a.author == "Phước Tuấn"
    assert "Phước Tuấn" not in a.content  # dòng tác giả bị bỏ khỏi nội dung
    assert "Chú thích ảnh" not in a.content
    assert a.category_raw == "Thời sự"  # article:section là mã số, lấy tên từ breadcrumb
    assert a.tags == ["cháy quán karaoke", "Tin nóng"]
    assert len(a.content.split("\n\n")) == 4


def test_tuoitre():
    a = get_parser("tuoitre").parse(
        fixture_text("tuoitre_article.html"),
        "https://tuoitre.vn/vu-cau-thu-miguel-qua-doi-100261108215707255.htm",
    )
    assert a.parse_ok
    assert a.author == "HUY ĐĂNG"
    assert a.category_raw == "Thể thao"
    assert a.published_at == "2026-11-08T22:02:00+07:00"
    assert a.sapo.startswith("Sau cái chết")
    assert "Đọc thêm" not in a.content


def test_dantri_uses_jsonld_and_densest_block():
    a = get_parser("dantri").parse(
        fixture_text("dantri_article.html"),
        "https://dantri.com.vn/thoi-su/hon-hai-gio-chua-chay-quan-karaoke-250m2-o-dong-nai-20261108212623865.htm",
    )
    assert a.parse_ok
    assert a.published_at == "2026-11-08T21:56:24+07:00"
    assert a.author == "Phước Tuần"
    assert a.category_raw == "Thời sự"
    assert a.sapo.startswith("Sau hơn 2 giờ")  # đã bỏ "(Dân trí) - "
    assert "Tin khác" not in a.content and "Mô tả ngắn" not in a.content
    assert "Ảnh:" not in a.content


def test_dantri_date_falls_back_to_id():
    html = fixture_text("dantri_article.html").replace('"datePublished":"2026-11-08T21:56:24+07:00",', "")
    a = get_parser("dantri").parse(html, "https://dantri.com.vn/thoi-su/x-20261108212623865.htm")
    assert a.published_at == "2026-11-08T21:26:23+07:00"


def test_short_page_is_not_ok():
    a = get_parser("vnexpress").parse("<html><head><title>x</title></head><body><p>ngắn</p></body></html>", "https://vnexpress.net/x-1.html")
    assert not a.parse_ok


def _vne_with_author(author_html: str):
    html = fixture_text("vnexpress_article.html").replace(
        '<p class="Normal" style="text-align:right;"><strong>Phước Tuấn</strong></p>',
        f'<p class="Normal" style="text-align:right;">{author_html}</p>',
    )
    return get_parser("vnexpress").parse(html, "https://vnexpress.net/x-5142197.html")


def test_vnexpress_author_with_source_note():
    # Gặp thật ngày 9/11: "Hồng Hạnh (Theo Tagesspiegel, Br )" còn sót trong nội dung.
    a = _vne_with_author("<strong>Hồng Hạnh</strong> (Theo <em>Tagesspiegel</em>, Br )")
    assert a.author == "Hồng Hạnh"
    assert "Tagesspiegel" not in a.content and "Hồng Hạnh" not in a.content


def test_vnexpress_author_with_prefix():
    # Gặp thật ngày 9/11: tác giả bị lấy thành "Bài & ảnh:".
    a = _vne_with_author("<strong>Bài &amp; ảnh:</strong> Nick M")
    assert a.author == "Nick M"
    assert "Nick M" not in a.content


def test_vnexpress_clean_author_variants():
    from crawler.parsers.sites import VnExpressParser as P

    assert P.clean_author("Ngọc Ánh (Theo AFP, AP )") == "Ngọc Ánh"
    assert P.clean_author("Bài và ảnh: Quỳnh Trang") == "Quỳnh Trang"
    assert P.clean_author("Lê Hoàng") == "Lê Hoàng"
    assert P.clean_author("Như Quỳnh (Tổng hợp)") == "Như Quỳnh"
