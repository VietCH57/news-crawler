"""Chuẩn hóa URL, tính article_id và content_hash."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Tham số chỉ dùng để theo dõi lượt truy cập, không đổi nội dung trang.
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "gidzl",
    "zarsrc",
    "zarsrcid",
    "ref",
    "refer",
    "source",
    "from",
    "s_cate",
}


def normalize_url(url: str) -> str:
    """Đưa URL về một dạng duy nhất: https, tên miền chữ thường, bỏ www.,
    bỏ fragment và các tham số theo dõi."""
    url = url.strip()
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    port = f":{parts.port}" if parts.port and parts.port not in (80, 443) else ""
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(_TRACKING_PREFIXES) and k.lower() not in _TRACKING_KEYS
    ]
    path = parts.path or "/"
    return urlunsplit(("https", host + port, path, urlencode(query), ""))


def extract_native_id(url: str, pattern: str | None) -> str | None:
    """Lấy mã số bài ở cuối URL theo regex của từng báo (nhóm thứ nhất)."""
    if not pattern:
        return None
    m = re.search(pattern, urlsplit(url).path)
    return m.group(1) if m else None


def make_article_id(source: str, url: str, id_pattern: str | None) -> str:
    """article_id = SHA-1 của ``source:mã_số``.

    Không băm cả URL vì khi báo sửa tiêu đề, phần chữ (slug) trong URL đổi theo
    nhưng mã số giữ nguyên. URL không có mã số thì mới băm cả URL đã chuẩn hóa.
    """
    native = extract_native_id(url, id_pattern)
    key = f"{source}:{native}" if native else normalize_url(url)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


_WS = re.compile(r"\s+")


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    return _WS.sub(" ", text).strip()


def content_hash(title: str | None, sapo: str | None, content: str | None) -> str:
    """Hash nội dung để phát hiện bài bị sửa. Bỏ qua khác biệt khoảng trắng."""
    joined = "\n".join(clean_text(x) for x in (title, sapo, content))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()
