"""Parser riêng cho từng báo.

Các selector dựa trên khảo sát ngày 8/11/2026. Khi báo đổi giao diện:
lưu một trang mẫu bằng ``python tools/save_fixture.py <url>``, sửa selector,
rồi tăng ``version`` để biết bản tin nào cần xử lý lại.
"""

from __future__ import annotations

import re

from selectolax.lexbor import LexborHTMLParser

from ..timeutil import date_from_digits
from .base import BaseParser, ParsedArticle


class VnExpressParser(BaseParser):
    name = "vnexpress"
    version = "1"
    sapo_selectors = ["p.description"]
    content_selectors = ["article.fck_detail p.Normal"]
    author_selectors = [
        "article.fck_detail p.author_mail strong",
        'article.fck_detail p[style*="right"] strong',
        "article.fck_detail p.author strong",
    ]

    def post_process(self, art: ParsedArticle, tree: LexborHTMLParser, news: dict, url: str) -> None:
        # Tên tác giả nằm trong đoạn p.Normal cuối cùng: bỏ khỏi nội dung.
        if art.author and not art.used_fallback:
            paras = art.content.split("\n\n")
            if paras and paras[-1].strip() == art.author.strip():
                art.content = "\n\n".join(paras[:-1])


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
