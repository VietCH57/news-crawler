"""Trang ảnh, video, podcast, đồ họa tương tác: mô phỏng bố cục khảo sát ngày 10/11/2026.

Các trang này ít hoặc không có chữ là thật. Parser phải gắn ``media_type`` và coi là đạt,
không dùng trafilatura (sẽ lấy nhầm khung đánh giá, tin liên quan).
"""

from crawler.parsers import get_parser

_JUNK = (
    "<div class='feedback'><p>Đánh giá trải nghiệm. Bạn thấy trải nghiệm của mình thế nào? "
    "Hãy cho chúng tôi biết để chúng tôi phục vụ bạn tốt hơn. Email sẽ được bảo mật, chúng tôi chỉ "
    "sử dụng để liên lạc khi cần thiết. Khi gửi, bạn đồng ý với điều khoản sử dụng và chính sách "
    "bảo mật. Cảm ơn bạn đã chia sẻ ý kiến với tòa soạn, ý kiến sẽ được xem xét kỹ.</p></div>"
)


def _page(title: str, body: str, body_cls: str = "") -> str:
    return (
        f'<html><head><meta property="og:title" content="{title}"/>'
        '<meta property="og:description" content="Mô tả ngắn của trang."/>'
        '<meta property="article:published_time" content="2026-11-10T06:00:00+07:00"/></head>'
        f'<body class="{body_cls}">{body}{_JUNK}</body></html>'
    )


def test_vnexpress_interactive_graphic():
    # 5141666: thân bài chỉ có widget đồ họa nạp bằng JavaScript.
    html = _page(
        "Vệ tinh sát thủ tung đòn thế nào",
        '<article class="fck_detail"><h1>Vệ tinh sát thủ</h1>'
        '<div class="component vne-gfx" data-component-type="widget_interactive"></div>'
        '<p style="text-align:right;"><strong>Đồ họa: Sơn Vũ</strong></p></article>',
    )
    a = get_parser("vnexpress").parse(html, "https://vnexpress.net/x-5141666.html")
    assert a.media_type == "interactive" and a.is_short and a.parse_ok
    assert a.extract_method != "trafilatura"


def test_vnexpress_podcast_with_transcript():
    paras = "".join(
        f'<p class="Normal">Đoạn lời thoại {i}: phe Dân chủ gia tăng sức ép về điều tra, ngân sách và nhân sự.</p>'
        for i in range(8)
    )
    body = (
        '<p class="description">Việc chiếm đa số tại lưỡng viện giúp phe Dân chủ.</p>'
        '<div class="wrap-content-tab-vod"><div class="tab-content tab-transcript active">'
        '<div class="tab-cheploi content-tab hidden"><div class="scrolling-ctn">'
        '<p class="Normal"><strong>Giới hạn quyền lực</strong></p>' + paras
        + "</div></div></div></div><audio src='a.mp3'></audio>"
    )
    a = get_parser("vnexpress").parse(
        _page("Nghe podcasts | Giới hạn quyền lực - Báo VnExpress", body),
        "https://vnexpress.net/x-5142229.html",
    )
    assert a.title == "Giới hạn quyền lực"
    assert a.content.startswith("Đoạn lời thoại 0")
    assert a.extract_method == "selector" and a.media_type == "text" and a.parse_ok


def test_vnexpress_podcast_headlines_only():
    body = (
        '<p class="description">Lao động vùng biên đổ xuống công trường.</p>'
        '<div class="wrap-content-tab-vod"><div class="tab-content tab-transcript active">'
        '<div class="tab-noidung content-tab"><ul class="time_stemp">'
        '<li><a data-time="28"><strong>00:28</strong> Lao động vùng biên đổ xuống công trường</a></li>'
        '<li><a data-time="65"><strong>01:05</strong> Nhiều nước bắt buộc ôtô điện phát âm thanh</a></li>'
        "</ul></div></div></div><audio src='a.mp3'></audio>"
    )
    a = get_parser("vnexpress").parse(_page("Nghe podcasts | Điểm tin 6h", body), "https://vnexpress.net/x-5142246.html")
    assert a.content.split("\n\n") == [
        "Lao động vùng biên đổ xuống công trường",
        "Nhiều nước bắt buộc ôtô điện phát âm thanh",
    ]
    assert a.media_type == "audio" and a.parse_ok and "Đánh giá" not in a.content


def test_vnexpress_podcast_without_text_skips_trafilatura():
    body = (
        '<div class="wrap-content-tab-vod"><div class="tab-content tab-transcript active">'
        '<div class="more-content-podcast"></div></div></div>'
    )
    a = get_parser("vnexpress").parse(_page("Nghe podcasts | Quyền lực tối thượng", body), "https://vnexpress.net/x-5141519.html")
    assert a.content == "" and a.media_type == "audio" and a.parse_ok


def test_tuoitre_video_takes_video_caption():
    body = (
        '<h2 class="detail-sapo">Niên vụ cà phê khép lại bằng những con số khả quan.</h2>'
        '<div class="detail-content afcbc-body">'
        '<div class="VCSortableInPreviewMode" type="VideoStream" data-vid="x.mp4">'
        '<div class="VideoCMS_Caption"><p>Bảo vệ Robusta trước thời tiết cực đoan</p></div></div>'
        '<div class="VCSortableInPreviewMode" type="RelatedNewsBox">'
        '<p class="VCObjectBoxRelatedNewsItemSapo">Tin liên quan không được lấy.</p></div></div>'
    )
    a = get_parser("tuoitre").parse(_page("Bảo vệ Robusta", body), "https://tuoitre.vn/x-100261107165046509.htm")
    assert a.content == "Bảo vệ Robusta trước thời tiết cực đoan"
    assert a.media_type == "video" and a.parse_ok


def test_vietnamnet_podcast_bulletin():
    body = (
        '<div class="content-detail content-detail-podcast"><div class="podcast-wrapper"></div>'
        '<h2 class="content-detail-sapo">Bản tin trưa 10/11: Tìm thấy thi thể người đàn ông.</h2>'
        '<div class="main-content maincontent"></div></div>'
    )
    a = get_parser("vietnamnet").parse(_page("Bản tin trưa 10/11", body), "https://vietnamnet.vn/x-2573345.html")
    assert a.media_type == "audio" and a.parse_ok and a.sapo.startswith("Bản tin trưa")


def test_tienphong_short_video_has_no_body():
    body = '<div class="short-video-wrapper"><video class="video_player"></video><h1 class="article__title">Sạt lở đá</h1></div>'
    a = get_parser("tienphong").parse(_page("Sạt lở đá tại đập Tarbela", body, "shortvideo-detail"), "https://tienphong.vn/x-post1893062.tpo")
    assert a.media_type == "video" and a.parse_ok
    assert a.extract_method == "selector" and "Đánh giá" not in a.content


def test_vietnamplus_photo_page_uses_slide_captions():
    slides = "".join(
        f"<div class='swiper-slide'><img/><figcaption>Ảnh {i}: biển xâm thực gây xói lở tại Cửa Việt.</figcaption></div>"
        for i in range(3)
    )
    body = (
        '<div class="article longform"><div class="article__picture">'
        f'<div class="swiper photo-detail-img">{slides}</div>'
        f'<div class="swiper photo-detail-thumb">{slides}</div></div>'
        '<div class="article__sapo cms-desc">Tình trạng xâm thực bờ biển.</div></div>'
    )
    a = get_parser("vietnamplus").parse(_page("Quảng Trị lo sạt lở", body, "photo-page"), "https://www.vietnamplus.vn/x-post1147322.vnp")
    assert a.extract_method == "selector+captions"
    assert a.content.count("Ảnh 0") == 1 and "Đánh giá" not in a.content
    assert a.media_type == "photo" and a.parse_ok


def test_long_article_with_embedded_video_stays_text():
    paras = "".join(f"<p>Đoạn {i} của một bài viết dài có nhúng video ở giữa bài, nội dung bình thường.</p>" for i in range(8))
    body = (
        '<div class="detail-content"><div class="VCSortableInPreviewMode" type="VideoStream"></div>'
        + paras + "</div>"
    )
    a = get_parser("tuoitre").parse(_page("Bài thường", body), "https://tuoitre.vn/x-100261107165046509.htm")
    assert a.media_type == "text" and not a.is_short and a.parse_ok


def test_empty_container_without_media_is_still_an_error():
    body = '<div class="detail-content"></div>'
    a = get_parser("tuoitre").parse(_page("Trang lỗi", body), "https://tuoitre.vn/x-100261107165046509.htm")
    assert a.media_type == "text" and not a.parse_ok


def test_vnexpress_quiz():
    html = _page(
        "Nước nào có nữ thủ tướng đầu tiên?",
        '<article class="fck_detail"><p class="description">Bà là người nước nào?</p>'
        '<div data-widget="obj"><div id="quiz-9435" data-component-type="quiz"></div></div>'
        '<p class="Normal" style="text-align:right;"><strong>Khánh Linh</strong></p></article>',
    )
    a = get_parser("vnexpress").parse(html, "https://vnexpress.net/x-5142584.html")
    assert a.media_type == "quiz" and a.parse_ok and a.author == "Khánh Linh"


def test_image_only_body_is_photo():
    # Kết quả xổ số SGGP, thông báo đấu giá Tuổi Trẻ, infographic VOV: chỉ có ảnh.
    body = '<div class="article__body"><figure><img data-src="kq.webp"/></figure><div class="article__author">XSKT</div></div>'
    a = get_parser("sggp").parse(_page("Kết quả xổ số", body), "https://www.sggp.org.vn/x-post881011.html")
    assert a.media_type == "photo" and a.parse_ok


def test_nhandan_emagazine():
    blocks = "".join(
        '<div class="Theme-Layer-BodyText"><div class="Theme-Layer-BodyText--inner">'
        + "".join(f"<p>Khối {b} đoạn {i}: nâng chuẩn chất lượng bệnh viện công tại thành phố.</p>" for i in range(4))
        + "</div></div>"
        for b in range(2)
    )
    a = get_parser("nhandan").parse(_page("Nâng chuẩn chất lượng bệnh viện công", blocks), "https://nhandan.vn/special/x/index.html")
    assert a.extract_method == "selector" and a.parse_ok
    assert a.content.startswith("Khối 0 đoạn 0") and "Khối 1 đoạn 3" in a.content and "Đánh giá" not in a.content


def test_vtc_video_pages():
    yt = '<div class="edittor-content"><p><iframe src="https://www.youtube.com/embed/abc"></iframe></p><div class="author-make">VTC News</div></div>'
    a = get_parser("vtcnews").parse(_page("Bê bối CEO", yt), "https://vtcnews.vn/x-ar1050185.html")
    assert a.media_type == "video" and a.parse_ok
    page = '<div class="nd-detail-video"><p>Ở ấp Lâm Dồ, anh Thạch Hoàng Minh chọn cách dạy dân làm giàu.</p></div>'
    a = get_parser("vtcnews").parse(_page("Chuyện chưa kể", page), "https://vtcnews.vn/x-ar1049411.html")
    assert a.content.startswith("Ở ấp Lâm Dồ") and a.media_type == "video" and a.extract_method == "selector"


def test_vov_video_summary():
    page = (
        '<article class="detail"><div class="video"><figcaption class="video-figcaption">'
        '<div class="video__summary"><p>Dàn cầu thủ U23 Việt Nam đối mặt lịch thi đấu dày đặc.</p></div>'
        "</figcaption></div></article>"
    )
    a = get_parser("vov").parse(_page("Tin bóng đá 10-11", page), "https://vov.vn/x-post1348927.vov")
    assert a.content.startswith("Dàn cầu thủ") and a.media_type == "video" and a.extract_method == "selector"


def test_dedicated_video_pages_never_use_trafilatura():
    # Trang video riêng: trafilatura từng lấy "Phím tắt điều khiển" (VnExpress),
    # chân trang (VietnamNet), tin Shorts khác (SGGP).
    junk = "".join(f"<p>Phím tắt điều khiển số {i}: tua video đi ba giây, mở thảo luận, tắt mở âm.</p>" for i in range(8))
    cases = [
        ("vnexpress", "page-short", "https://vnexpress.net/x-5142890.html", ""),
        ("vietnamnet", "wrapper-page-video", "https://vietnamnet.vn/x-2572993.html",
         '<div class="video-detail"><div class="video-detail__media"></div>'
         '<div class="video-detail__sabo"><p>CHILE – Đoạn video ghi lại sự việc hy hữu.</p></div></div>'),
        ("sggp", "", "https://www.sggp.org.vn/x-post881009.html", '<div class="box-short-video-detail"></div>'),
    ]
    for source, body_cls, url, body in cases:
        a = get_parser(source).parse(_page("Video", body + f"<main><article>{junk}</article></main>", body_cls), url)
        assert a.extract_method != "trafilatura", source
        assert a.media_type == "video" and a.parse_ok, source
    assert a.sapo  # SGGP: sapo lấy từ og:description
