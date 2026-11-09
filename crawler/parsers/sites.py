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
    version = "2"
    sapo_selectors = ["p.description"]
    content_selectors = ["article.fck_detail p.Normal"]
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
    version = "1"
    sapo_selectors = [".detail-sapo", "h2.detail-sapo"]
    content_selectors = [".detail-content > p", ".detail-content p", ".detail-cmain p"]
    author_selectors = [".detail-author .name", ".author-info .name", ".detail-author"]
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


PARSERS: dict[str, type[BaseParser]] = {
    "vnexpress": VnExpressParser,
    "tuoitre": TuoiTreParser,
    "dantri": DanTriParser,
}


def get_parser(name: str, min_words: int = 50) -> BaseParser:
    cls = PARSERS.get(name, BaseParser)
    return cls(min_words=min_words)
