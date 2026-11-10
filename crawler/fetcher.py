"""Tải trang: tuân theo robots.txt, giới hạn tốc độ theo từng báo."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger(__name__)

ROBOTS_TTL_SECONDS = 24 * 3600


class RateLimiter:
    """Bảo đảm hai request liên tiếp cách nhau ít nhất 1/rate giây."""

    def __init__(self, rate: float):
        self.base_rate = rate
        self.rate = rate
        self._next = 0.0
        self._lock = asyncio.Lock()
        self._slow_until = 0.0

    def slow_down(self, factor: float = 0.5, seconds: float = 3600) -> None:
        """Giảm tốc khi bị báo trả 429."""
        self.rate = max(self.base_rate * factor, 0.05)
        self._slow_until = time.monotonic() + seconds
        log.warning("Giảm tốc xuống %.2f req/s trong %.0f giây", self.rate, seconds)

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if self._slow_until and now > self._slow_until:
                self.rate, self._slow_until = self.base_rate, 0.0
            delay = self._next - now
            if delay > 0:
                await asyncio.sleep(delay)
            self._next = max(now, self._next) + 1.0 / self.rate


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int  # 0 = lỗi mạng / timeout
    text: str
    error: str | None = None
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class Fetcher:
    def __init__(
        self,
        user_agent: str,
        timeout: float,
        rates: dict[str, float],
        backfill_rates: dict[str, float],
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.user_agent = user_agent
        # Phần tên dùng để so khớp trong robots.txt, ví dụ "news-crawler".
        self.robots_agent = user_agent.split("/")[0].strip()
        self.client = httpx.AsyncClient(
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "vi-VN,vi;q=0.9",
            },
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )
        self.limiters = {d: RateLimiter(r) for d, r in rates.items()}
        self.backfill_limiters = {d: RateLimiter(r) for d, r in backfill_rates.items()}
        self.healths: dict[str, DomainHealth] = {}
        self._robots: dict[str, tuple[float, RobotFileParser | None]] = {}
        self._robots_lock = asyncio.Lock()

    async def close(self) -> None:
        await self.client.aclose()

    @staticmethod
    def domain_of(url: str) -> str:
        host = (urlsplit(url).hostname or "").lower()
        return host[4:] if host.startswith("www.") else host

    def _key(self, domain: str) -> str:
        """Tên miền con (thethao.sggp.org.vn) dùng chung giới hạn với tên miền đã cấu hình."""
        if domain in self.limiters:
            return domain
        for d in self.limiters:
            if domain.endswith("." + d):
                return d
        return domain

    def _limiter(self, domain: str) -> RateLimiter:
        domain = self._key(domain)
        if domain not in self.limiters:
            self.limiters[domain] = RateLimiter(1.0)
        return self.limiters[domain]

    def _apply_crawl_delay(self, domain: str, parser: RobotFileParser) -> None:
        try:
            delay = parser.crawl_delay(self.robots_agent)
        except Exception:  # noqa: BLE001
            delay = None
        if not delay:
            return
        cap = 1.0 / float(delay)
        for limiters in (self.limiters, self.backfill_limiters):
            lim = limiters.get(self._key(domain))
            if lim and lim.base_rate > cap:
                lim.base_rate = lim.rate = cap
        log.info("[%s] robots.txt yêu cầu Crawl-delay %ss: giới hạn %.3f request/giây", domain, delay, cap)

    async def _robots_for(self, domain: str) -> RobotFileParser | None:
        async with self._robots_lock:
            cached = self._robots.get(domain)
            if cached and time.monotonic() - cached[0] < ROBOTS_TTL_SECONDS:
                return cached[1]
            parser: RobotFileParser | None = None
            try:
                await self._limiter(domain).wait()
                r = await self.client.get(f"https://{domain}/robots.txt")
                if r.status_code == 200:
                    parser = RobotFileParser()
                    parser.parse(r.text.splitlines())
                    self._apply_crawl_delay(domain, parser)
                elif 400 <= r.status_code < 500:
                    parser = None  # không có robots.txt: được phép
                else:
                    raise httpx.HTTPStatusError("robots", request=r.request, response=r)
            except Exception as e:  # noqa: BLE001
                log.warning("Không đọc được robots.txt của %s: %s", domain, e)
                if isinstance(e, httpx.TransportError):
                    self.health(domain).failure()
                # Lỗi tạm thời: thử lại sau 10 phút, trong lúc đó coi như được phép.
                self._robots[domain] = (time.monotonic() - ROBOTS_TTL_SECONDS + 600, None)
                return None
            self._robots[domain] = (time.monotonic(), parser)
            return parser

    async def allowed(self, url: str) -> bool:
        parser = await self._robots_for(self.domain_of(url))  # robots.txt của chính tên miền đó
        return True if parser is None else parser.can_fetch(self.robots_agent, url)

    def health(self, domain: str) -> "DomainHealth":
        domain = self._key(domain)
        if domain not in self.healths:
            self.healths[domain] = DomainHealth(domain)
        return self.healths[domain]

    def paused_for(self, domain: str) -> float:
        """Số giây còn lại trước khi được gửi request tới báo này (0 = không tạm dừng)."""
        return self.health(domain).remaining()

    async def fetch(self, url: str, backfill: bool = False) -> FetchResult:
        domain = self.domain_of(url)
        health = self.health(domain)
        if health.remaining() > 0:
            return FetchResult(url, url, 0, "", error=PAUSED)
        if not await self.allowed(url):
            return FetchResult(url, url, 0, "", error="robots.txt không cho phép")
        bkey = self._key(domain)
        if backfill and bkey in self.backfill_limiters:
            await self.backfill_limiters[bkey].wait()
        limiter = self._limiter(domain)
        await limiter.wait()
        t0 = time.monotonic()
        try:
            r = await self.client.get(url)
        except httpx.HTTPError as e:
            health.failure()
            return FetchResult(url, url, 0, "", error=f"{type(e).__name__}: {e}")
        health.success()
        elapsed = time.monotonic() - t0
        if r.status_code == 429:
            limiter.slow_down()
            if backfill and bkey in self.backfill_limiters:
                self.backfill_limiters[bkey].slow_down()
        return FetchResult(url, str(r.url), r.status_code, r.text, elapsed=elapsed)


PAUSED = "domain paused"


class DomainHealth:
    """Tạm dừng một báo khi lỗi kết nối liên tiếp (thường là bị chặn IP hoặc trang sập).

    Sau ``threshold`` lỗi liên tiếp: nghỉ ``base_pause`` giây, mỗi lần thử lại vẫn lỗi
    thì nghỉ gấp đôi, tối đa ``max_pause``. Trong lúc nghỉ không gửi request nào,
    các bài trong hàng đợi chờ chứ không bị đánh dấu thất bại.
    """

    def __init__(self, domain: str, threshold: int = 5, base_pause: float = 900, max_pause: float = 4 * 3600):
        self.domain = domain
        self.threshold = threshold
        self.base_pause = base_pause
        self.max_pause = max_pause
        self.consecutive = 0
        self.pause_seconds = 0.0
        self.paused_until = 0.0
        self.down_since: float | None = None  # time.time() lúc bắt đầu lỗi
        self.recovered_after: float | None = None  # đặt khi vừa kết nối lại được

    def remaining(self) -> float:
        return max(0.0, self.paused_until - time.monotonic())

    def failure(self) -> None:
        self.consecutive += 1
        if self.down_since is None:
            self.down_since = time.time()
        if self.consecutive >= self.threshold:
            self.pause_seconds = (
                self.base_pause if not self.pause_seconds else min(self.pause_seconds * 2, self.max_pause)
            )
            self.paused_until = time.monotonic() + self.pause_seconds
            self.consecutive = 0
            log.warning(
                "[%s] %d lỗi kết nối liên tiếp: tạm dừng %d phút (có thể bị chặn IP hoặc trang sập)",
                self.domain, self.threshold, self.pause_seconds // 60,
            )

    def success(self) -> None:
        if self.down_since is not None and self.pause_seconds:
            self.recovered_after = time.time() - self.down_since
            log.info("[%s] Kết nối lại được sau %.0f phút", self.domain, self.recovered_after / 60)
        self.consecutive = 0
        self.pause_seconds = 0.0
        self.paused_until = 0.0
        self.down_since = None
