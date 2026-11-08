"""Trạng thái crawler trong SQLite: hàng đợi URL, bài đã thấy, các mốc.

Mọi thứ cần để chạy tiếp sau khi tắt máy đều nằm ở đây, nên crawler có thể
dừng bất kỳ lúc nào.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .timeutil import from_iso, now, to_iso

# Độ ưu tiên: số nhỏ được xử lý trước.
PRIORITY = {"rss": 0, "recheck": 1, "gap": 1, "backfill": 2}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    article_id      TEXT PRIMARY KEY,
    source          TEXT NOT NULL,
    url             TEXT NOT NULL,
    first_seen      TEXT NOT NULL,
    last_checked    TEXT NOT NULL,
    content_hash    TEXT,
    version_no      INTEGER NOT NULL DEFAULT 1,
    removed         INTEGER NOT NULL DEFAULT 0,
    recheck_idx     INTEGER NOT NULL DEFAULT 0,
    next_recheck_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_recheck ON articles(next_recheck_at);

CREATE TABLE IF NOT EXISTS frontier (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id  TEXT NOT NULL,
    url         TEXT NOT NULL,
    source      TEXT NOT NULL,
    kind        TEXT NOT NULL,          -- rss | recheck | gap | backfill
    priority    INTEGER NOT NULL,
    status      TEXT NOT NULL,          -- pending | inflight | failed
    attempts    INTEGER NOT NULL DEFAULT 0,
    next_try_at TEXT NOT NULL,
    hint        TEXT,                   -- JSON: thông tin lấy từ RSS/sitemap
    last_error  TEXT,
    created_at  TEXT NOT NULL
);
-- Không để cùng một bài nằm chờ hai lần với cùng loại việc.
CREATE UNIQUE INDEX IF NOT EXISTS uq_frontier_active
    ON frontier(article_id, kind) WHERE status IN ('pending', 'inflight');
CREATE INDEX IF NOT EXISTS idx_frontier_pick
    ON frontier(source, status, priority, next_try_at);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class Task:
    id: int
    article_id: str
    url: str
    source: str
    kind: str
    attempts: int
    hint: dict[str, Any]


@dataclass
class ArticleState:
    article_id: str
    source: str
    url: str
    first_seen: str
    content_hash: str | None
    version_no: int
    removed: bool
    recheck_idx: int


class State:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(_SCHEMA)
        # Việc đang dở khi máy tắt đột ngột được trả lại hàng đợi.
        self.db.execute("UPDATE frontier SET status='pending' WHERE status='inflight'")

    def close(self) -> None:
        self.db.close()

    # ---------- kv ----------
    def get(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    # ---------- articles ----------
    def seen(self, article_id: str) -> bool:
        return (
            self.db.execute("SELECT 1 FROM articles WHERE article_id=?", (article_id,)).fetchone()
            is not None
        )

    def get_article(self, article_id: str) -> ArticleState | None:
        r = self.db.execute("SELECT * FROM articles WHERE article_id=?", (article_id,)).fetchone()
        if not r:
            return None
        return ArticleState(
            article_id=r["article_id"],
            source=r["source"],
            url=r["url"],
            first_seen=r["first_seen"],
            content_hash=r["content_hash"],
            version_no=r["version_no"],
            removed=bool(r["removed"]),
            recheck_idx=r["recheck_idx"],
        )

    def add_article(
        self,
        article_id: str,
        source: str,
        url: str,
        content_hash: str | None,
        crawled_at: str,
        next_recheck_at: str | None,
    ) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO articles(article_id, source, url, first_seen, last_checked, "
            "content_hash, version_no, next_recheck_at) VALUES(?,?,?,?,?,?,1,?)",
            (article_id, source, url, crawled_at, crawled_at, content_hash, next_recheck_at),
        )

    def update_after_recheck(
        self,
        article_id: str,
        checked_at: str,
        content_hash: str | None,
        version_no: int,
        removed: bool,
        recheck_idx: int,
        next_recheck_at: str | None,
    ) -> None:
        self.db.execute(
            "UPDATE articles SET last_checked=?, content_hash=COALESCE(?, content_hash), "
            "version_no=?, removed=?, recheck_idx=?, next_recheck_at=? WHERE article_id=?",
            (
                checked_at,
                content_hash,
                version_no,
                int(removed),
                recheck_idx,
                next_recheck_at,
                article_id,
            ),
        )

    def due_rechecks(self, at: datetime, limit: int = 500) -> list[ArticleState]:
        rows = self.db.execute(
            "SELECT article_id FROM articles WHERE next_recheck_at IS NOT NULL "
            "AND next_recheck_at <= ? AND removed=0 ORDER BY next_recheck_at LIMIT ?",
            (to_iso(at), limit),
        ).fetchall()
        out = []
        for r in rows:
            a = self.get_article(r["article_id"])
            if a:
                out.append(a)
        return out

    def count_articles(self, source: str | None = None) -> int:
        if source:
            return self.db.execute(
                "SELECT COUNT(*) FROM articles WHERE source=?", (source,)
            ).fetchone()[0]
        return self.db.execute("SELECT COUNT(*) FROM articles").fetchone()[0]

    # ---------- frontier ----------
    def enqueue(
        self,
        article_id: str,
        url: str,
        source: str,
        kind: str,
        hint: dict[str, Any] | None = None,
        not_before: datetime | None = None,
    ) -> bool:
        """Đưa một URL vào hàng đợi. Trả về False nếu đã có việc giống hệt đang chờ."""
        ts = to_iso(not_before or now())
        cur = self.db.execute(
            "INSERT OR IGNORE INTO frontier(article_id, url, source, kind, priority, status, "
            "attempts, next_try_at, hint, created_at) VALUES(?,?,?,?,?,'pending',0,?,?,?)",
            (
                article_id,
                url,
                source,
                kind,
                PRIORITY[kind],
                ts,
                json.dumps(hint or {}, ensure_ascii=False),
                to_iso(now()),
            ),
        )
        return cur.rowcount > 0

    def claim(self, source: str, at: datetime | None = None, kinds: tuple[str, ...] | None = None) -> Task | None:
        """Lấy việc ưu tiên cao nhất đã đến hạn của một báo và đánh dấu đang xử lý."""
        ts = to_iso(at or now())
        sql = (
            "SELECT * FROM frontier WHERE source=? AND status='pending' AND next_try_at<=? "
        )
        params: list[Any] = [source, ts]
        if kinds:
            sql += f"AND kind IN ({','.join('?' * len(kinds))}) "
            params.extend(kinds)
        sql += "ORDER BY priority, next_try_at, id LIMIT 1"
        r = self.db.execute(sql, params).fetchone()
        if not r:
            return None
        self.db.execute("UPDATE frontier SET status='inflight' WHERE id=?", (r["id"],))
        return Task(
            id=r["id"],
            article_id=r["article_id"],
            url=r["url"],
            source=r["source"],
            kind=r["kind"],
            attempts=r["attempts"],
            hint=json.loads(r["hint"] or "{}"),
        )

    def done(self, task_id: int) -> None:
        self.db.execute("DELETE FROM frontier WHERE id=?", (task_id,))

    def retry(self, task_id: int, delay_seconds: float, error: str) -> None:
        self.db.execute(
            "UPDATE frontier SET status='pending', attempts=attempts+1, next_try_at=?, "
            "last_error=? WHERE id=?",
            (to_iso(now() + timedelta(seconds=delay_seconds)), error[:500], task_id),
        )

    def fail(self, task_id: int, error: str) -> None:
        self.db.execute(
            "UPDATE frontier SET status='failed', attempts=attempts+1, last_error=? WHERE id=?",
            (error[:500], task_id),
        )

    def pending_count(self, source: str | None = None, kinds: tuple[str, ...] | None = None) -> int:
        sql = "SELECT COUNT(*) FROM frontier WHERE status IN ('pending','inflight')"
        params: list[Any] = []
        if source:
            sql += " AND source=?"
            params.append(source)
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            params.extend(kinds)
        return self.db.execute(sql, params).fetchone()[0]

    def next_due_at(self, source: str) -> datetime | None:
        r = self.db.execute(
            "SELECT MIN(next_try_at) FROM frontier WHERE source=? AND status='pending'", (source,)
        ).fetchone()
        return from_iso(r[0]) if r and r[0] else None
