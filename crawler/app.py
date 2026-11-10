"""Ghép các thành phần: vòng RSS, worker theo báo, recheck, crawl ngược, nhịp tim."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from . import __version__
from .config import Config, SourceConfig
from .discover import backfill, rss
from .fetcher import PAUSED, Fetcher, FetchResult
from .normalize import content_hash, make_article_id, normalize_url
from .parsers import get_parser
from .parsers.base import CORE_VERSION
from .sinks import FileSink, Sink
from .state import State, Task
from .stats import Stats
from .timeutil import from_iso, now, now_iso, parse_date, to_iso

log = logging.getLogger(__name__)

BACKFILL_KINDS = ("backfill", "gap")


class Crawler:
    def __init__(
        self,
        cfg: Config,
        transport: httpx.AsyncBaseTransport | None = None,
        sink: Sink | None = None,
    ):
        self.cfg = cfg
        self.data = cfg.data_dir
        self.state = State(self.data / "state" / "crawler.db")
        self.stats = Stats(self.data)
        self.sink: Sink = sink or FileSink(
            self.data, fsync=cfg.fsync, on_rotate=self.stats.file_written
        )
        self.fetcher = Fetcher(
            cfg.user_agent,
            cfg.request_timeout,
            rates={s.domain: s.rate_limit for s in cfg.enabled_sources},
            backfill_rates={s.domain: s.backfill_rate for s in cfg.enabled_sources},
            transport=transport,
        )
        self.parsers = {s.name: get_parser(s.parser, cfg.min_words) for s in cfg.enabled_sources}
        self.sources = {s.name: s for s in cfg.enabled_sources}
        self.started = now()
        self._stop = asyncio.Event()
        self._alerted: dict[str, datetime] = {}
        self._feed_polled: dict[str, float] = {}

    # ------------------------------------------------------------------ vòng đời
    async def close(self) -> None:
        try:
            self.sink.close()
        finally:
            self.stats.flush()
            self.state.set("heartbeat", now_iso())
            await self.fetcher.close()
            self.state.close()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Chạy liên tục cho đến khi bị dừng (Ctrl+C)."""
        log.info("news-crawler %s bắt đầu, dữ liệu tại %s", __version__, self.data)
        self.detect_gap()
        tasks = [
            asyncio.create_task(self._rss_loop(), name="rss"),
            asyncio.create_task(self._recheck_loop(), name="recheck"),
            asyncio.create_task(self._heartbeat_loop(), name="heartbeat"),
        ]
        for src in self.sources.values():
            tasks.append(asyncio.create_task(self._worker(src), name=f"worker:{src.name}"))
            tasks.append(asyncio.create_task(self._backfill_loop(src), name=f"backfill:{src.name}"))
        try:
            await self._stop.wait()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def once(self, limit: int = 20) -> dict[str, int]:
        """Đọc RSS một lần rồi tải tối đa ``limit`` bài mỗi báo. Dùng để chạy thử."""
        await asyncio.gather(*(self._poll(src, all_feeds=True) for src in self.sources.values()))
        done: dict[str, int] = {}

        async def drain(src: SourceConfig) -> None:
            n = 0
            while n < limit:
                task = self.state.claim(src.name, kinds=("rss",))
                if task is None:
                    break
                await self.process(task)
                n += 1
            done[src.name] = n

        await asyncio.gather(*(drain(s) for s in self.sources.values()))
        return done

    # ------------------------------------------------------------------ các vòng lặp
    def _feeds_due(self, src: SourceConfig) -> list[str]:
        """Feed đầu tiên (tin mới nhất) đọc mỗi ``rss_interval_seconds``;
        feed chuyên mục chỉ là lưới an toàn nên đọc thưa hơn."""
        t = time.monotonic()
        due = []
        for i, feed in enumerate(src.feeds):
            first = src.feed_interval_seconds or self.cfg.rss_interval_seconds
            interval = first if i == 0 else self.cfg.category_feed_interval_seconds
            last = self._feed_polled.get(feed)
            if last is None or t - last >= interval - 1:
                due.append(feed)
        return due

    def _since(self, src: SourceConfig) -> str | None:
        """Khởi động nhẹ: báo chưa có bài nào thì chốt mốc "chỉ lấy bài đăng từ N giờ trước
        thời điểm bật". Mốc lưu trong State DB và áp dụng mãi về sau (bài cũ hơn để cho
        crawl ngược), không chỉ cho lần đọc RSS đầu tiên."""
        key = f"since:{src.name}"
        since = self.state.get(key)
        if since is None and self.cfg.warm_start_hours and self.state.count_articles(src.name) == 0:
            since = to_iso(now() - timedelta(hours=self.cfg.warm_start_hours))
            self.state.set(key, since)
            log.info("[%s] Báo mới: chỉ lấy bài đăng từ %s", src.name, since)
        return since

    async def _poll(self, src: SourceConfig, all_feeds: bool = False) -> None:
        if self.fetcher.paused_for(src.domain) > 0:
            return
        feeds = src.feeds if all_feeds else self._feeds_due(src)
        if not feeds:
            return
        try:
            await rss.poll_source(src, self.fetcher, self.state, self.stats, feeds, since=self._since(src))
            t = time.monotonic()
            for f in feeds:
                self._feed_polled[f] = t
        except Exception:  # noqa: BLE001
            log.exception("[%s] Lỗi khi đọc RSS", src.name)

    async def _rss_loop(self) -> None:
        while True:
            await asyncio.gather(*(self._poll(s) for s in self.sources.values()))
            await asyncio.sleep(self.cfg.rss_interval_seconds)

    async def _worker(self, src: SourceConfig) -> None:
        while True:
            paused = self.fetcher.paused_for(src.domain)
            if paused > 0:
                await asyncio.sleep(min(paused, 60))
                continue
            task = self.state.claim(src.name)
            if task is None:
                await asyncio.sleep(1)
                continue
            try:
                await self.process(task)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.exception("[%s] Lỗi khi xử lý %s", src.name, task.url)
                self._retry_or_fail(task, f"exception: {e}")

    async def _recheck_loop(self) -> None:
        while True:
            for a in self.state.due_rechecks(now()):
                if a.source in self.sources:
                    self.state.enqueue(a.article_id, a.url, a.source, "recheck")
            await asyncio.sleep(60)

    async def _heartbeat_loop(self) -> None:
        while True:
            self.state.set("heartbeat", now_iso())
            self._check_recoveries()
            self.stats.flush()
            self._check_alerts()
            await asyncio.sleep(60)

    def _check_recoveries(self) -> None:
        """Một báo vừa kết nối lại sau thời gian bị chặn/sập: lấy bù bài của
        những ngày đó từ sitemap và thử lại các việc đã thất bại vì lỗi mạng."""
        for src in self.sources.values():
            h = self.fetcher.health(src.domain)
            if h.recovered_after is None:
                continue
            outage = h.recovered_after
            h.recovered_after = None
            start = now() - timedelta(seconds=outage)
            self.stats.gap(to_iso(start), now_iso(), source=src.name, reason="không kết nối được")
            n = self.state.requeue_failed(src.name)
            log.info("[%s] Đưa lại %d việc thất bại vào hàng đợi", src.name, n)
            if self.cfg.gap_fill and src.backfill.method and outage >= self.cfg.gap_threshold_minutes * 60:
                key = f"gap_days:{src.name}"
                days = {d.isoformat() for d in backfill.days_between(start.date(), now().date())}
                self.state.set(key, sorted(set(self.state.get(key, [])) | days, reverse=True))
                log.info("[%s] Lên lịch lấp khoảng trống %s", src.name, sorted(days))

    async def _backfill_loop(self, src: SourceConfig) -> None:
        if not src.backfill.method:
            return
        while True:
            try:
                worked = await self._backfill_step(src)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("[%s] Lỗi khi liệt kê bài cũ", src.name)
                worked = False
            await asyncio.sleep(5 if worked else 60)

    async def _backfill_step(self, src: SourceConfig) -> bool:
        """Liệt kê bài của một ngày (ưu tiên ngày bị lỡ do tắt máy). Trả về True nếu có việc."""
        if self.fetcher.paused_for(src.domain) > 0:
            return False
        if self.state.pending_count(src.name, BACKFILL_KINDS) >= self.cfg.backfill.max_pending:
            return False

        gap_key = f"gap_days:{src.name}"
        gap_days: list[str] = self.state.get(gap_key, [])
        if gap_days:
            day = date.fromisoformat(gap_days[0])
            n = await self._enqueue_day(src, day, "gap")
            self.state.set(gap_key, gap_days[1:])
            log.info("[%s] Lấp khoảng trống ngày %s: %d URL", src.name, day, n)
            return True

        if not self.cfg.backfill.enabled or self.cfg.backfill.until is None:
            return False
        cur_key = f"backfill_cursor:{src.name}"
        cursor = self.state.get(cur_key)
        day = date.fromisoformat(cursor) if cursor else now().date() - timedelta(days=1)
        if day < self.cfg.backfill.until:
            return False
        n = await self._enqueue_day(src, day, "backfill")
        self.state.set(cur_key, (day - timedelta(days=1)).isoformat())
        log.info("[%s] Crawl ngược ngày %s: %d URL mới", src.name, day, n)
        if day - timedelta(days=1) < self.cfg.backfill.until:
            log.info("[%s] Đã crawl ngược tới %s", src.name, self.cfg.backfill.until)
        return True

    async def _enqueue_day(self, src: SourceConfig, day: date, kind: str) -> int:
        urls = await backfill.list_day(self.fetcher, src, day)
        n = 0
        for u in urls:
            url = normalize_url(u, src.host)
            if self.fetcher.domain_of(url) != src.domain:
                continue
            aid = make_article_id(src.name, url, src.id_pattern)
            if self.state.seen(aid):
                continue
            if self.state.enqueue(aid, url, src.name, kind, {"listed_day": day.isoformat()}):
                n += 1
        if n:
            self.stats.incr(src.name, "discovered", n)
        return n

    def detect_gap(self) -> None:
        """Nếu lần chạy trước dừng quá lâu, ghi nhận khoảng trống và lên lịch lấp."""
        hb = self.state.get("heartbeat")
        if not hb:
            return
        last = from_iso(hb)
        gap = now() - last
        if gap < timedelta(minutes=self.cfg.gap_threshold_minutes):
            return
        log.warning("Crawler đã dừng %s (từ %s)", str(gap).split(".")[0], hb)
        self.stats.gap(hb, now_iso())
        if not self.cfg.gap_fill:
            return
        days = backfill.days_between(last.date(), now().date())
        for src in self.sources.values():
            if not src.backfill.method:
                continue
            key = f"gap_days:{src.name}"
            existing = self.state.get(key, [])
            merged = sorted(set(existing) | {d.isoformat() for d in days}, reverse=True)
            self.state.set(key, merged)

    def _check_alerts(self) -> None:
        t = now()
        if not (6 <= t.hour < 23) or t - self.started < timedelta(hours=2):
            return
        for name in self.sources:
            last = self.stats.last_written_at(name)
            stale = last is None or t - from_iso(last) > timedelta(hours=2)
            if stale and t - self._alerted.get(name, t - timedelta(days=1)) > timedelta(hours=1):
                log.warning("[%s] Không có bài mới nào trong 2 giờ: báo đổi giao diện hoặc đang chặn?", name)
                self._alerted[name] = t

    # ------------------------------------------------------------------ xử lý một việc
    def _retry_or_fail(self, task: Task, error: str) -> None:
        delays = self.cfg.retry_delays_seconds or [60]
        if task.attempts < self.cfg.max_retries:
            delay = delays[min(task.attempts, len(delays) - 1)]
            self.state.retry(task.id, delay, error)
        else:
            log.warning("[%s] Bỏ %s sau %d lần thử: %s", task.source, task.url, task.attempts + 1, error)
            self.state.fail(task.id, error)

    def _next_recheck(self, first_seen: str, idx: int) -> str | None:
        hours = self.cfg.recheck_after_hours
        if idx >= len(hours):
            return None
        return to_iso(from_iso(first_seen) + timedelta(hours=hours[idx]))

    async def process(self, task: Task) -> None:
        src = self.sources[task.source]
        res = await self.fetcher.fetch(task.url, backfill=task.kind in BACKFILL_KINDS)
        self.stats.incr(src.name, "fetched")

        if res.status == 0 and res.error == PAUSED:
            # Báo đang tạm dừng: việc chưa được gửi đi, chờ chứ không tính là thất bại.
            self.state.retry(task.id, max(self.fetcher.paused_for(src.domain), 30), PAUSED, count=False)
            return
        if res.status == 0:
            if res.error and "robots" in res.error:
                self.state.fail(task.id, res.error)
            else:
                self.stats.incr(src.name, "network_errors")
                self._retry_or_fail(task, res.error or "network error")
            return
        if res.status in (404, 410):
            self.stats.incr(src.name, "http_4xx")
            if task.kind == "recheck":
                self._write_removed(task, res)
                self.state.done(task.id)
            else:
                self.state.fail(task.id, f"HTTP {res.status}")
            return
        if res.status == 429 or res.status >= 500:
            self.stats.incr(src.name, "http_5xx" if res.status >= 500 else "http_4xx")
            self._retry_or_fail(task, f"HTTP {res.status}")
            return
        if not res.ok:
            self.stats.incr(src.name, "http_4xx")
            self.state.fail(task.id, f"HTTP {res.status}")
            return

        self._handle_page(task, src, res)
        self.state.done(task.id)

    def _handle_page(self, task: Task, src: SourceConfig, res: FetchResult) -> None:
        parser = self.parsers[src.name]
        final_url = normalize_url(res.final_url, src.host)
        parsed = parser.parse(res.text, final_url)
        chash = content_hash(parsed.title, parsed.sapo, parsed.content)
        crawled_at = now_iso()
        self.stats.incr(src.name, "html_bytes", len(res.text.encode("utf-8")))

        if task.kind == "recheck":
            a = self.state.get_article(task.article_id)
            if a is None:
                return
            idx = a.recheck_idx + 1
            nxt = self._next_recheck(a.first_seen, idx)
            self.stats.incr(src.name, "rechecked")
            if chash == a.content_hash:
                self.state.update_after_recheck(
                    a.article_id, crawled_at, None, a.version_no, False, idx, nxt
                )
                return
            version = a.version_no + 1
            self._write(task, src, parser, parsed, res, final_url, chash, crawled_at, version)
            self.stats.incr(src.name, "changed")
            self.state.update_after_recheck(a.article_id, crawled_at, chash, version, False, idx, nxt)
            return

        if self.state.seen(task.article_id):
            return  # đã lấy được qua đường khác trong lúc chờ
        self._write(task, src, parser, parsed, res, final_url, chash, crawled_at, 1)
        next_recheck = self._next_recheck(crawled_at, 0) if task.kind == "rss" else None
        self.state.add_article(task.article_id, src.name, final_url, chash, crawled_at, next_recheck)

    def _write(self, task, src, parser, parsed, res, final_url, chash, crawled_at, version) -> None:
        published = parsed.published_at or parse_date(task.hint.get("rss_published"))
        record: dict[str, Any] = {
            "article_id": task.article_id,
            "url": final_url,
            "source": src.name,
            "crawl_kind": task.kind,
            "version_no": version,
            "content_hash": chash,
            "is_removed": False,
            "title": parsed.title or task.hint.get("rss_title", ""),
            "sapo": parsed.sapo,
            "content": parsed.content,
            "author": parsed.author,
            "category_raw": parsed.category_raw or task.hint.get("rss_category"),
            "tags": parsed.tags,
            "published_at": published,
            "crawled_at": crawled_at,
            "http_status": res.status,
            "parse_ok": parsed.parse_ok,
            "extract_method": parsed.extract_method,
            "word_count": parsed.word_count,
            "is_short": parsed.is_short,
            "parser_version": f"{parser.name}-{parser.version}/core{CORE_VERSION}",
            "html": res.text,
        }
        # Ghi file trước, cập nhật State DB sau: sập giữa chừng thì có thể trùng, không mất.
        self.sink.write(record)
        self.stats.incr(src.name, "written")
        if not parsed.parse_ok:
            self.stats.incr(src.name, "parse_failed")
            log.warning("[%s] Trích xuất chưa đạt (%d từ): %s", src.name, parsed.word_count, final_url)
        if parsed.used_fallback:
            self.stats.incr(src.name, "fallback_used")
        if parsed.is_short:
            self.stats.incr(src.name, "short_content")

    def _write_removed(self, task: Task, res: FetchResult) -> None:
        a = self.state.get_article(task.article_id)
        if a is None:
            return
        crawled_at = now_iso()
        record = {
            "article_id": a.article_id,
            "url": a.url,
            "source": a.source,
            "crawl_kind": "recheck",
            "version_no": a.version_no + 1,
            "content_hash": None,
            "is_removed": True,
            "title": "",
            "sapo": "",
            "content": "",
            "author": None,
            "category_raw": None,
            "tags": [],
            "published_at": None,
            "crawled_at": crawled_at,
            "http_status": res.status,
            "parse_ok": False,
            "extract_method": None,
            "word_count": 0,
            "is_short": False,
            "parser_version": None,
            "html": None,
        }
        self.sink.write(record)
        self.stats.incr(a.source, "removed")
        self.state.update_after_recheck(
            a.article_id, crawled_at, None, a.version_no + 1, True, a.recheck_idx + 1, None
        )
        log.info("[%s] Bài bị gỡ: %s", a.source, a.url)
