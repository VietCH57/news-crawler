from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def cfg(tmp_path):
    """Cấu hình thật (config.yaml) nhưng dữ liệu ghi vào thư mục tạm."""
    from crawler.config import load_config

    c = load_config(ROOT / "config.yaml")
    c.data_dir = tmp_path / "data"
    c.retry_delays_seconds = [0, 0, 0]
    return c
