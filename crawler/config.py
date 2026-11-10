"""Đọc config.yaml thành các dataclass."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml


@dataclass
class BackfillSourceConfig:
    method: str | None = None
    category_ids: list[int] = field(default_factory=list)


@dataclass
class SourceConfig:
    name: str
    domain: str
    id_pattern: str
    parser: str
    feeds: list[str]
    rate_limit: float = 1.0
    backfill_rate: float = 0.5
    enabled: bool = True
    backfill: BackfillSourceConfig = field(default_factory=BackfillSourceConfig)


@dataclass
class BackfillConfig:
    enabled: bool = False
    until: date | None = None
    max_pending: int = 200


@dataclass
class Config:
    user_agent: str
    data_dir: Path
    rss_interval_seconds: int = 300
    category_feed_interval_seconds: int = 1800
    recheck_after_hours: list[float] = field(default_factory=lambda: [1, 6, 24])
    request_timeout: float = 20.0
    max_retries: int = 3
    retry_delays_seconds: list[int] = field(default_factory=lambda: [60, 300, 1800])
    gap_fill: bool = True
    gap_threshold_minutes: int = 30
    min_words: int = 50
    fsync: bool = False
    backfill: BackfillConfig = field(default_factory=BackfillConfig)
    sources: dict[str, SourceConfig] = field(default_factory=dict)

    @property
    def enabled_sources(self) -> list[SourceConfig]:
        return [s for s in self.sources.values() if s.enabled]


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def load_config(path: str | Path) -> Config:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    sources: dict[str, SourceConfig] = {}
    for name, s in (raw.get("sources") or {}).items():
        bf = s.get("backfill") or {}
        sources[name] = SourceConfig(
            name=name,
            domain=s["domain"],
            id_pattern=s["id_pattern"],
            parser=s.get("parser", name),
            feeds=list(s.get("feeds") or []),
            rate_limit=float(s.get("rate_limit", 1.0)),
            backfill_rate=float(s.get("backfill_rate", 0.5)),
            enabled=bool(s.get("enabled", True)),
            backfill=BackfillSourceConfig(
                method=bf.get("method"),
                category_ids=[int(x) for x in (bf.get("category_ids") or [])],
            ),
        )

    bf = raw.get("backfill") or {}
    data_dir = Path(raw.get("data_dir", "data"))
    if not data_dir.is_absolute():
        data_dir = (path.parent / data_dir).resolve()

    return Config(
        user_agent=raw["user_agent"],
        data_dir=data_dir,
        rss_interval_seconds=int(raw.get("rss_interval_seconds", 300)),
        category_feed_interval_seconds=int(raw.get("category_feed_interval_seconds", 1800)),
        recheck_after_hours=[float(x) for x in raw.get("recheck_after_hours", [1, 6, 24])],
        request_timeout=float(raw.get("request_timeout", 20)),
        max_retries=int(raw.get("max_retries", 3)),
        retry_delays_seconds=[int(x) for x in raw.get("retry_delays_seconds", [60, 300, 1800])],
        gap_fill=bool(raw.get("gap_fill", True)),
        gap_threshold_minutes=int(raw.get("gap_threshold_minutes", 30)),
        min_words=int(raw.get("min_words", 50)),
        fsync=bool(raw.get("fsync", False)),
        backfill=BackfillConfig(
            enabled=bool(bf.get("enabled", False)),
            until=_as_date(bf.get("until")),
            max_pending=int(bf.get("max_pending", 200)),
        ),
        sources=sources,
    )
