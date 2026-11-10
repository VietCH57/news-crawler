"""Phần trích xuất dùng chung cho mọi báo.

Thứ tự ưu tiên: JSON-LD ``NewsArticle`` và thẻ meta (ổn định hơn giao diện),
rồi CSS selector riêng của từng báo, cuối cùng là trafilatura.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from selectolax.lexbor import LexborHTMLParser, LexborNode

from ..normalize import clean_text
from ..timeutil import parse_date

log = logging.getLogger(__name__)

try:  # trafilatura là tùy chọn: thiếu thì bỏ qua bước dự phòng
    import trafilatura
except ImportError:  # pragma: no cover
    trafilatura = None


# Tăng khi đổi logic chung của trích xuất (ghép vào parser_version của bản tin).
CORE_VERSION = "3"


@dataclass
class ParsedArticle:
    title: str = ""
    sapo: str = ""
    content: str = ""
    author: str | None = None
    category_raw: str | None = None
    tags: list[str] = field(default_factory=list)
    published_at: str | None = None
    parse_ok: bool = False
    # selector | selector+captions (bài ảnh/video: thêm chú thích ảnh) | trafilatura (bố cục lạ)
    extract_method: str = "selector"
    is_short: bool = False  # bài ảnh, video, infographic: chữ ít hơn min_words
    # Chỉ xét khi bài ngắn: text | photo | video | audio | interactive | quiz.
    # Bài dài (kể cả podcast có lời thoại) luôn là "text".
    media_type: str = "text"

    @property
    def used_fallback(self) -> bool:
        return self.extract_method == "trafilatura"

    @property
    def word_count(self) -> int:
        return len(self.content.split())


_ARTICLE_TYPES = {"NewsArticle", "Article", "ReportageNewsArticle", "AnalysisNewsArticle"}


def _flatten_jsonld(obj: Any) -> Iterable[dict]:
    if isinstance(obj, list):
        for x in obj:
            yield from _flatten_jsonld(x)
    elif isinstance(obj, dict):
        if "@graph" in obj:
            yield from _flatten_jsonld(obj["@graph"])
        yield obj


def _types(obj: dict) -> set[str]:
    t = obj.get("@type")
    if isinstance(t, list):
        return {str(x) for x in t}
    return {str(t)} if t else set()


class BaseParser:
    """Lớp cha. Các báo ghi đè các selector hoặc phương thức cần thiết."""

    name = "base"
    version = "1"
    sapo_selectors: list[str] = []
    content_selectors: list[str] = []  # selector trả về các đoạn văn <p>
    author_selectors: list[str] = []
    drop_selectors: list[str] = []  # phần tử bỏ khỏi nội dung (chú thích ảnh, quảng cáo...)
    sapo_prefixes: list[str] = []  # tiền tố bỏ khỏi sapo, ví dụ "(Dân trí) - "
    # Khung chứa thân bài. Tìm thấy khung thì KHÔNG dùng trafilatura: với bài ảnh/infographic,
    # trafilatura lấy nhầm cột tin liên quan (gặp ở VietnamPlus ngày 10/11/2026).
    container_selectors: list[str] = []
    caption_selectors: list[str] = [
        "figcaption", ".caption", ".PhotoCMS_Caption", ".VideoCMS_Caption", ".image-caption"
    ]
    # Dấu hiệu trang ảnh/video/podcast/đồ họa (khảo sát 10/11/2026). Xét theo thứ tự;
    # chỉ dùng khi bài ít chữ, vì bài thường cũng có thể nhúng video.
    media_markers: dict[str, list[str]] = {
        "quiz": ['[data-component-type="quiz"]'],  # câu đố VnExpress, nạp bằng JavaScript
        "interactive": ['[data-component-type="widget_interactive"]', ".vne-gfx"],
        "audio": [".content-detail-podcast", ".tab-transcript", ".wrap-content-tab-vod", "audio"],
        "video": [
            # Trang video riêng: VnExpress (page-short), VietnamNet, Tiền Phong, SGGP Shorts.
            "body.page-short", "body.wrapper-page-video", ".video-detail__media",
            "body.video-page", "body.shortvideo-detail", ".video-player",
            ".detail_shortvideo", ".box-short-video-detail", ".short-video-slider",
            ".nd-detail-video", "b.video-element", ".video__summary",
            '[type="VideoStream"]', 'iframe[src*="youtube.com/embed"]', "video",
        ],
        "photo": ["body.photo-page", ".photo-detail-img"],
    }

    def __init__(self, min_words: int = 50):
        self.min_words = min_words

    # ---------- tiện ích ----------
    @staticmethod
    def meta(tree: LexborHTMLParser, *names: str) -> str | None:
        for n in names:
            for attr in ("property", "name", "itemprop"):
                node = tree.css_first(f'meta[{attr}="{n}"]')
                if node is not None:
                    v = node.attributes.get("content")
                    if v and v.strip():
                        return v.strip()
        return None

    @staticmethod
    def jsonld(tree: LexborHTMLParser) -> list[dict]:
        out: list[dict] = []
        for node in tree.css('script[type="application/ld+json"]'):
            raw = node.text(strip=True)
            if not raw:
                continue
            try:
                out.extend(_flatten_jsonld(json.loads(raw)))
            except json.JSONDecodeError:
                # Một số trang để dấu phẩy thừa hoặc ký tự điều khiển; bỏ qua khối đó.
                continue
        return out

    @staticmethod
    def first_text(tree: LexborHTMLParser, selectors: list[str]) -> str:
        for sel in selectors:
            node = tree.css_first(sel)
            if node is not None:
                t = clean_text(node.text(separator=" ", strip=True))
                if t:
                    return t
        return ""

    def paragraphs(self, tree: LexborHTMLParser) -> list[str]:
        for sel in self.content_selectors:
            nodes = tree.css(sel)
            paras = [clean_text(n.text(separator=" ", strip=True)) for n in nodes]
            paras = [p for p in paras if p]
            if paras:
                return paras
        return []

    @staticmethod
    def densest_paragraphs(tree: LexborHTMLParser) -> list[str]:
        """Tìm phần tử có nhiều chữ nhất trong các thẻ <p> con trực tiếp.
        Dùng cho báo có class CSS sinh tự động, khó viết selector cố định."""
        best: LexborNode | None = None
        best_len = 0
        parents: dict[int, tuple[LexborNode, int]] = {}
        for p in tree.css("p"):
            parent = p.parent
            if parent is None:
                continue
            length = len(p.text(strip=True))
            node, total = parents.get(parent.mem_id, (parent, 0))
            parents[parent.mem_id] = (node, total + length)
        for node, total in parents.values():
            if total > best_len:
                best, best_len = node, total
        if best is None:
            return []
        paras = [clean_text(p.text(separator=" ", strip=True)) for p in best.css("p")]
        return [p for p in paras if p]

    # ---------- trích xuất ----------
    def _container_and_captions(self, tree: LexborHTMLParser) -> tuple[bool, list[str], bool]:
        """(có khung bài, chú thích ảnh, khung có ảnh)."""
        for sel in self.container_selectors:
            box = tree.css_first(sel)
            if box is None:
                continue
            has_img = box.css_first("img, figure, [data-large-src]") is not None
            caps: list[str] = []
            for csel in self.caption_selectors:
                for node in box.css(csel):
                    t = clean_text(node.text(separator=" ", strip=True))
                    if t and t not in caps:
                        caps.append(t)
            return True, caps, has_img
        return False, [], False

    def detect_media(self, tree: LexborHTMLParser) -> str:
        for kind, selectors in self.media_markers.items():
            for sel in selectors:
                if tree.css_first(sel) is not None:
                    return kind
        return "text"

    def parse(self, html: str, url: str) -> ParsedArticle:
        tree = LexborHTMLParser(html)
        # Xét trước khi bỏ phần tử: Tuổi Trẻ đặt video trong khối bị bỏ.
        media = self.detect_media(tree)
        # Lấy chú thích ảnh trước khi bỏ <figure> (cần cho bài ảnh).
        has_container, captions, has_img = self._container_and_captions(tree)
        for sel in self.drop_selectors:
            for node in tree.css(sel):
                node.decompose()

        ld_all = self.jsonld(tree)
        news = next((o for o in ld_all if _types(o) & _ARTICLE_TYPES), {})
        crumbs = next((o for o in ld_all if "BreadcrumbList" in _types(o)), {})

        art = ParsedArticle()
        art.title = clean_text(
            news.get("headline") or self.meta(tree, "og:title") or self.first_text(tree, ["h1"])
        )
        art.sapo = self.first_text(tree, self.sapo_selectors) or clean_text(
            news.get("description") or self.meta(tree, "og:description", "description")
        )
        for prefix in self.sapo_prefixes:
            if art.sapo.startswith(prefix):
                art.sapo = art.sapo[len(prefix):].strip()

        art.published_at = parse_date(
            self.meta(tree, "article:published_time", "datePublished", "pubdate")
            or news.get("datePublished")
        )
        art.author = self.first_text(tree, self.author_selectors) or self._ld_author(news) or None
        art.category_raw = (
            self.meta(tree, "article:section")
            or (news.get("articleSection") if isinstance(news.get("articleSection"), str) else None)
            or self._crumb_category(crumbs)
        )
        if art.category_raw and art.category_raw.isdigit():
            # VnExpress đặt mã số chuyên mục vào article:section; lấy tên từ breadcrumb.
            art.category_raw = self._crumb_category(crumbs) or art.category_raw
        art.tags = self._tags(tree, news)

        paras = self.paragraphs(tree)
        art.content = "\n\n".join(paras)
        if len(art.content.split()) < self.min_words:
            if has_container:
                # Bài ảnh/video/infographic: ít chữ là thật, bổ sung chú thích ảnh.
                extra = [c for c in captions if c not in art.content]
                if extra:
                    art.content = "\n\n".join(paras + extra)
                    art.extract_method = "selector+captions"
            elif media == "text" and trafilatura is not None:
                # Trang video/podcast không có khung bài: trafilatura chỉ lấy được rác
                # (khung đánh giá, tin liên quan), nên chỉ dùng cho bố cục lạ thật sự.
                extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
                if extracted and len(extracted.split()) > len(art.content.split()):
                    art.content = extracted.strip()
                    art.extract_method = "trafilatura"

        self.post_process(art, tree, news, url)
        art.is_short = art.word_count < self.min_words
        if media == "text" and has_container and has_img and art.word_count == 0:
            # Thân bài chỉ có ảnh, không chú thích: infographic, thông báo, kết quả xổ số.
            media = "photo"
        art.media_type = media if art.is_short else "text"
        # Đạt nếu có tiêu đề và: đủ chữ; hoặc bài ngắn lấy đúng từ khung bài; hoặc là trang
        # ảnh/video/podcast/đồ họa (ít hoặc không có chữ là thật, sapo vẫn có).
        art.parse_ok = bool(art.title) and (
            art.word_count >= self.min_words
            or (has_container and art.word_count > 0)
            or art.media_type != "text"
        )
        return art

    def post_process(self, art: ParsedArticle, tree: LexborHTMLParser, news: dict, url: str) -> None:
        """Điểm mở rộng cho từng báo."""

    @staticmethod
    def _ld_author(news: dict) -> str:
        a = news.get("author")
        if isinstance(a, list):
            names = [x.get("name", "") if isinstance(x, dict) else str(x) for x in a]
            return clean_text(", ".join(n for n in names if n))
        if isinstance(a, dict):
            return clean_text(a.get("name", ""))
        return clean_text(a) if isinstance(a, str) else ""

    @staticmethod
    def _crumb_category(crumbs: dict) -> str | None:
        items = crumbs.get("itemListElement") or []
        names: list[str] = []
        for it in items:
            # Dân trí bọc mỗi phần tử trong một mảng riêng.
            if isinstance(it, list) and it:
                it = it[0]
            if not isinstance(it, dict):
                continue
            item = it.get("item")
            name = it.get("name") or (item.get("name") if isinstance(item, dict) else None)
            if name and clean_text(name).lower() != "trang chủ":
                names.append(clean_text(name))
        return names[0] if names else None

    def _tags(self, tree: LexborHTMLParser, news: dict) -> list[str]:
        raw = self.meta(tree, "news_keywords", "keywords") or news.get("keywords") or ""
        if isinstance(raw, list):
            items = raw
        else:
            items = str(raw).split(",")
        tags = []
        for t in items:
            t = clean_text(str(t))
            if t and t not in tags:
                tags.append(t)
        return tags
