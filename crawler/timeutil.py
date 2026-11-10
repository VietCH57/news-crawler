"""Xử lý thời gian. Mọi mốc thời gian được lưu dạng ISO 8601 với múi giờ +07:00."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# Dùng offset cố định thay vì zoneinfo để chạy được trên Windows mà không cần tzdata.
VN_TZ = timezone(timedelta(hours=7))


def now() -> datetime:
    return datetime.now(VN_TZ)


def now_iso() -> str:
    return to_iso(now())


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=VN_TZ)
    return dt.astimezone(VN_TZ).isoformat(timespec="seconds")


def from_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


_US_FORMAT = re.compile(r"^\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}(:\d{2})? ?(AM|PM)$", re.I)


def parse_date(value: str | None) -> str | None:
    """Đọc ngày giờ từ nhiều định dạng các báo dùng, trả về ISO +07:00 hoặc None.

    Hỗ trợ:
    - ISO 8601 (thẻ meta, JSON-LD): ``2026-11-08T22:02:00+07:00``, ``...Z``
    - RFC 822 (RSS chuẩn): ``Sun, 08 Nov 2026 22:08:17 +0700``
    - Kiểu Mỹ của RSS Tuổi Trẻ, không có múi giờ: ``11/8/2026 8:08:00 PM``
      (coi là giờ Việt Nam)
    """
    if not value:
        return None
    s = value.strip()
    if not s:
        return None

    # ISO 8601 (SGGP viết múi giờ "+0700" không có dấu hai chấm)
    try:
        iso = s.replace("Z", "+00:00") if s.endswith("Z") else s
        iso = re.sub(r"T(.*)([+-]\d{2})(\d{2})$", r"T\1\2:\3", iso)
        dt = datetime.fromisoformat(iso)
        return to_iso(dt)
    except ValueError:
        pass

    # Kiểu Mỹ (Tuổi Trẻ)
    if _US_FORMAT.match(s):
        for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p"):
            try:
                return to_iso(datetime.strptime(s, fmt).replace(tzinfo=VN_TZ))
            except ValueError:
                continue

    # RFC 822; một số báo viết "GMT+7" thay vì "+0700"
    rfc = re.sub(r"GMT\+(\d{1,2})$", lambda m: f"+{int(m.group(1)):02d}00", s)
    try:
        dt = parsedate_to_datetime(rfc)
        if dt is not None:
            return to_iso(dt)
    except (TypeError, ValueError, IndexError):
        pass
    return None


def date_from_digits(digits: str) -> str | None:
    """Đọc thời điểm từ mã số bài dạng ``yyyyMMddHHmmss...`` (Dân trí)."""
    if len(digits) < 14 or not digits[:14].isdigit():
        return None
    try:
        dt = datetime.strptime(digits[:14], "%Y%m%d%H%M%S").replace(tzinfo=VN_TZ)
    except ValueError:
        return None
    return to_iso(dt)
