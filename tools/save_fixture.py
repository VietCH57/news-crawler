"""Lưu một trang bài thật vào tests/fixtures/real/ để thử parser offline.

    python tools/save_fixture.py https://vnexpress.net/...-5142197.html
    python -m crawler parse tests/fixtures/real/vnexpress_5142197.html --source vnexpress

Thư mục tests/fixtures/real/ nằm trong .gitignore: không đưa trang của báo lên repo công khai.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crawler.config import load_config  # noqa: E402
from crawler.fetcher import Fetcher  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    url = sys.argv[1]
    cfg = load_config(ROOT / "config.yaml")
    domain = Fetcher.domain_of(url)
    src = next((s for s in cfg.sources.values() if s.domain == domain), None)
    name = src.name if src else domain.replace(".", "_")
    m = re.search(r"-(\d+)\.html?$", url)
    out_dir = ROOT / "tests" / "fixtures" / "real"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{name}_{m.group(1) if m else 'page'}.html"
    r = httpx.get(url, headers={"User-Agent": cfg.user_agent}, follow_redirects=True, timeout=30)
    r.raise_for_status()
    out.write_text(r.text, encoding="utf-8")
    print(f"Đã lưu {out} ({len(r.content) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
