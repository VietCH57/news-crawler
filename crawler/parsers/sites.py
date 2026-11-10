"""Parser riêng cho từng báo.

Các selector dựa trên khảo sát ngày 8/11/2026. Khi báo đổi giao diện:
lưu một trang mẫu bằng ``python tools/save_fixture.py <url>``, sửa selector,
rồi tăng ``version`` để biết bản tin nào cần xử lý lại.
"""

from __future__ import annotations

import re

from selectolax.lexbor import LexborHTMLParser

from ..normalize import clean_text
from ..timeutil import date_from_digits
from .base import BaseParser, ParsedArticle


class VnExpressParser(BaseParser):
    """Tác giả VnExpress nằm ở đoạn căn phải cuối bài, có nhiều dạng:
    ``<strong>Hồng Hạnh</strong> (Theo Tagesspiegel, Br)``,
    ``<strong>Bài & ảnh:</strong> Nick M``, ``<strong>Lê Hoàng</strong>``."""

    name = "vnexpress"
    version = "3"
    sapo_selectors = ["p.description"]
    # Trang podcast (tiêu đề "Nghe podcasts | ..."): lời thoại nằm ở tab "Chép lời",
    # trang lặp khối này hai lần nên chỉ lấy khối có class content-tab.
    content_selectors = [
        "article.fck_detail p.Normal",
        ".tab-cheploi.content-tab .scrolling-ctn > p",
    ]
    container_selectors = ["article.fck_detail", ".tab-transcript"]
    # Podcast điểm tin không có lời thoại: lấy danh sách tin theo mốc thời gian.
    caption_selectors = BaseParser.caption_selectors + [".time_stemp li a"]
    _TITLE_NOISE = re.compile(r"^nghe podcasts?\s*\|\s*|\s*-\s*báo vnexpress\s*$", re.I)
    _TIMESTAMP = re.compile(r"(?m)^\d{1,2}:\d{2}(:\d{2})?\s+")
    author_selectors: list[str] = []  # xử lý riêng trong post_process

    _AUTHOR_NODES = [
        'article.fck_detail p[style*="right"]',
        "article.fck_detail p.author_mail",
        "article.fck_detail p.author",
    ]
    _PREFIX = re.compile(
        r"^(bài\s*(&|và|,)\s*ảnh|bài|ảnh|video|thực hiện|nhóm phóng viên|tin|đồ họa)\s*:\s*",
        re.I,
    )
    _SOURCE = re.compile(r"\s*\(\s*(theo|tổng hợp|nguồn)\b[^)]*\)?\s*$", re.I)

    @classmethod
    def clean_author(cls, text: str) -> str:
        t = clean_text(text)
        t = cls._SOURCE.sub("", t)
        t = cls._PREFIX.sub("", t)
        return t.strip(" -–,;:")

    def post_process(self, art: ParsedArticle, tree: LexborHTMLParser, news: dict, url: str) -> None:
        # Podcast: og:title có dạng "Nghe podcasts | <tiêu đề> - Báo VnExpress";
        # đoạn đầu lời thoại lặp lại tiêu đề; mục điểm tin có mốc "00:28".
        art.title = self._TITLE_NOISE.sub("", art.title).strip()
        paras = art.content.split("\n\n")
        if paras and art.title and clean_text(paras[0]) == art.title:
            art.content = "\n\n".join(paras[1:])
        art.content = self._TIMESTAMP.sub("", art.content)

        node = None
        for sel in self._AUTHOR_NODES:
            nodes = tree.css(sel)
            if nodes:
                node = nodes[-1]
                break
        if node is None:
            return
        raw = clean_text(node.text(separator=" ", strip=True))
        name = self.clean_author(raw)
        if name and len(name) <= 80:
            art.author = name
        # Bỏ dòng tác giả khỏi nội dung.
        if raw and not art.used_fallback:
            paras = [p for p in art.content.split("\n\n") if clean_text(p) != raw]
            art.content = "\n\n".join(paras)


class TuoiTreParser(BaseParser):
    name = "tuoitre"
    version = "2"
    sapo_selectors = [".detail-sapo", "h2.detail-sapo"]
    content_selectors = [".detail-content > p", ".detail-content p", ".detail-cmain p"]
    author_selectors = [".detail-author .name", ".author-info .name", ".detail-author"]
    container_selectors = [".detail-content", ".detail-cmain"]
    drop_selectors = [
        ".detail-content .VCSortableInPreviewMode",  # khung tin liên quan, ảnh, video
        ".detail-content figure",
        ".detail-content table",
    ]


class DanTriParser(BaseParser):
    """Dân trí dùng class CSS sinh tự động (Tailwind) và không có thẻ meta ngày đăng:
    nội dung lấy theo khối có nhiều chữ nhất, ngày đăng lấy từ JSON-LD hoặc mã số bài."""

    name = "dantri"
    version = "1"
    sapo_selectors = [".singular-sapo", "h2.singular-sapo"]
    author_selectors = [".author-name"]
    sapo_prefixes = ["(Dân trí) - ", "(Dân trí) -", "(Dân trí)- ", "(Dân trí) "]
    drop_selectors = ["figure", "figcaption", "aside"]

    _ID = re.compile(r"-(\d{14,})\.htm$")

    def paragraphs(self, tree: LexborHTMLParser) -> list[str]:
        paras = super().paragraphs(tree)
        return paras or self.densest_paragraphs(tree)

    def post_process(self, art: ParsedArticle, tree: LexborHTMLParser, news: dict, url: str) -> None:
        if not art.published_at:
            m = self._ID.search(url)
            if m:
                art.published_at = date_from_digits(m.group(1))


class ThanhNienParser(TuoiTreParser):
    """Thanh Niên dùng cùng nền tảng với Tuổi Trẻ (detail-sapo, detail-content)."""

    name = "thanhnien"
    version = "1"
    content_selectors = [".detail-content > p", ".detail-content p", ".detail-cmain p"]


class ZceParser(BaseParser):
    """Nền tảng chung của Nhân Dân, VietnamPlus, Tiền Phong (khảo sát 10/11/2026):
    ``.article__sapo``, ``.article__body.zce-content-body``, ``.article__meta``."""

    name = "zce"
    version = "2"
    sapo_selectors = [".article__sapo"]
    content_selectors = [".article__body > p", ".article__body p"]
    # Trang ảnh (body.photo-page) không có .article__body: nội dung là chú thích ảnh.
    container_selectors = [".article__body", ".photo-detail-img"]
    author_selectors = [".article__meta .cms-author", ".article__meta .wrap-author", ".article__author"]
    drop_selectors = [".article__body figure", ".article__body table", ".article__body .box-related"]
    sapo_prefixes = ["TPO - ", "TPO-", "(TTXVN/Vietnam+) - ", "NDO - ", "NDĐT - "]


class NhanDanParser(ZceParser):
    name = "nhandan"


class VietnamPlusParser(ZceParser):
    name = "vietnamplus"


class TienPhongParser(ZceParser):
    name = "tienphong"


class VietnamNetParser(BaseParser):
    """Tác giả lấy từ JSON-LD: khối tác giả trên trang có thêm chữ "Xem các bài viết của tác giả"."""

    name = "vietnamnet"
    version = "1"
    sapo_selectors = [".content-detail-sapo"]
    content_selectors = ["#maincontent > p", ".maincontent > p", ".maincontent p"]
    container_selectors = ["#maincontent", ".maincontent"]
    drop_selectors = ["#maincontent figure", "#maincontent table"]


class VtcParser(BaseParser):
    """Sapo và tác giả lấy từ JSON-LD (selector trên trang lẫn quảng cáo)."""

    name = "vtcnews"
    version = "1"
    content_selectors = [".edittor-content > p", ".edittor-content p"]
    container_selectors = [".edittor-content"]
    drop_selectors = [".edittor-content figure", ".edittor-content table", ".edittor-content .sponsored-banner"]


class LaoDongParser(BaseParser):
    name = "laodong"
    version = "1"
    content_selectors = ["#gallery-ctt > p", ".art-body > p", ".art-body p"]
    container_selectors = ["#gallery-ctt", ".art-body"]
    author_selectors = [".art-info .authors", "span.authors"]
    drop_selectors = [".art-body figure", ".art-body table"]


class SggpParser(ZceParser):
    """Sài Gòn Giải Phóng: nền tảng như Nhân Dân. Ô tác giả trên trang ghi nguồn ("SGGPO"),
    nên tác giả lấy từ JSON-LD."""

    name = "sggp"
    author_selectors: list[str] = []
    sapo_prefixes = ["SGGPO - ", "SGGP - "]


class VovParser(BaseParser):
    """VOV: tác giả dạng "Trọng Phú/VOV.VN", sapo bắt đầu bằng "VOV.VN - "."""

    name = "vov"
    version = "1"
    sapo_selectors = [".article-summary"]
    content_selectors = [".text-long > p", ".article-content p"]
    container_selectors = [".text-long", ".article-content"]
    drop_selectors = [".text-long figure", ".text-long table"]
    sapo_prefixes = ["VOV.VN - ", "VOV.VN -", "VOV - "]
    _SUFFIX = re.compile(r"\s*/\s*VOV(\.VN)?\s*$", re.I)

    def post_process(self, art: ParsedArticle, tree: LexborHTMLParser, news: dict, url: str) -> None:
        if art.author:
            art.author = self._SUFFIX.sub("", art.author).strip() or art.author


PARSERS: dict[str, type[BaseParser]] = {
    "sggp": SggpParser,
    "vov": VovParser,
    "vnexpress": VnExpressParser,
    "tuoitre": TuoiTreParser,
    "dantri": DanTriParser,
    "thanhnien": ThanhNienParser,
    "nhandan": NhanDanParser,
    "vietnamplus": VietnamPlusParser,
    "tienphong": TienPhongParser,
    "vietnamnet": VietnamNetParser,
    "vtcnews": VtcParser,
    "laodong": LaoDongParser,
}


def get_parser(name: str, min_words: int = 50) -> BaseParser:
    cls = PARSERS.get(name, BaseParser)
    return cls(min_words=min_words)
