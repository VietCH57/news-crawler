from crawler.normalize import content_hash, extract_native_id, make_article_id, normalize_url
from crawler.timeutil import date_from_digits, parse_date

VNE = r"-(\d+)\.html$"
HTM = r"-(\d+)\.htm$"


def test_normalize_strips_tracking_and_fragment():
    u = "http://WWW.VnExpress.net/bai-viet-5142197.html?utm_source=rss&utm_medium=x#box-comment"
    assert normalize_url(u) == "https://vnexpress.net/bai-viet-5142197.html"


def test_normalize_keeps_other_params():
    assert normalize_url("https://a.vn/x?page=2&fbclid=abc") == "https://a.vn/x?page=2"


def test_native_ids_of_three_sources():
    assert extract_native_id("https://vnexpress.net/a-b-5142216.html", VNE) == "5142216"
    assert extract_native_id("https://tuoitre.vn/a-b-100261108215707255.htm", HTM) == "100261108215707255"
    assert extract_native_id("https://dantri.com.vn/thoi-su/a-20261108212623865.htm", HTM) == "20261108212623865"


def test_article_id_survives_slug_change():
    # Bài Dân trí thật đã đổi tiêu đề (và slug) sau khi đăng, mã số giữ nguyên.
    old = "https://dantri.com.vn/thoi-su/hai-gio-chua-chay-quan-karaoke-hon-250m2-o-dong-nai-20261108212623865.htm"
    new = "https://dantri.com.vn/thoi-su/hon-hai-gio-chua-chay-quan-karaoke-250m2-o-dong-nai-20261108212623865.htm"
    assert make_article_id("dantri", old, HTM) == make_article_id("dantri", new, HTM)
    assert make_article_id("dantri", old, HTM) != make_article_id("tuoitre", old, HTM)


def test_article_id_falls_back_to_url():
    a = make_article_id("x", "https://a.vn/khong-co-ma", HTM)
    b = make_article_id("x", "https://a.vn/khong-co-ma?utm_source=z", HTM)
    assert a == b and len(a) == 40


def test_content_hash_ignores_whitespace():
    assert content_hash("A  b", "c\n", "d") == content_hash("A b", " c", "d ")
    assert content_hash("A", "c", "d") != content_hash("A", "c", "e")


def test_parse_date_formats():
    assert parse_date("Sun, 08 Nov 2026 22:08:17 +0700") == "2026-11-08T22:08:17+07:00"
    assert parse_date("2026-11-08T22:02:00+07:00") == "2026-11-08T22:02:00+07:00"
    assert parse_date("2026-11-08T15:02:00Z") == "2026-11-08T22:02:00+07:00"
    # RSS Tuổi Trẻ: kiểu Mỹ, không có múi giờ -> giờ Việt Nam
    assert parse_date("11/8/2026 8:08:00 PM") == "2026-11-08T20:08:00+07:00"
    assert parse_date("Sun, 08 Nov 2026 20:27:35 GMT+7") == "2026-11-08T20:27:35+07:00"
    assert parse_date("") is None and parse_date("không phải ngày") is None


def test_date_from_dantri_id():
    assert date_from_digits("20261108212623865") == "2026-11-08T21:26:23+07:00"
    assert date_from_digits("123") is None
