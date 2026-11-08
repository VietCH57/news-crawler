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
        self._robots: dict[str, tuple[float, RobotFileParser | None]] = {}
        self._robots_lock = asyncio.Lock()

    async def close(self) -> None:
        await self.client.aclose()

    @staticmethod
    def domain_of(url: str) -> str:
        host = (urlsplit(url).hostname or "").lower()
        return host[4:] if host.startswith("www.") else host

    def _limiter(self, domain: str) -> RateLimiter:
        if domain not in self.limiters:
            self.limiters[domain] = RateLimiter(1.0)
        return self.limiters[domain]

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
                elif 400 <= r.status_code < 500:
                    parser = None  # không có robots.txt: được phép
                else:
                    raise httpx.HTTPStatusError("robots", request=r.request, response=r)
            except Exception as e:  # noqa: BLE001
                log.warning("Không đọc được robots.txt của %s: %s", domain, e)
                # Lỗi tạm thời: thử lại sau 10 phút, trong lúc đó coi như được phép.
                self._robots[domain] = (time.monotonic() - ROBOTS_TTL_SECONDS + 600, None)
                return None
            self._robots[domain] = (time.monotonic(), parser)
            return parser

    async def allowed(self, url: str) -> bool:
        parser = await self._robots_for(self.domain_of(url))
        return True if parser is None else parser.can_fetch(self.robots_agent, url)

    async def fetch(self, url: str, backfill: bool = False) -> FetchResult:
        domain = self.domain_of(url)
        if not await self.allowed(url):
            return FetchResult(url, url, 0, "", error="robots.txt không cho phép")
        if backfill and domain in self.backfill_limiters:
            await self.backfill_limiters[domain].wait()
        limiter = self._limiter(domain)
        await limiter.wait()
        t0 = time.monotonic()
        try:
            r = await self.client.get(url)
        except httpx.HTTPError as e:
            return FetchResult(url, url, 0, "", error=f"{type(e).__name__}: {e}")
        elapsed = time.monotonic() - t0
        if r.status_code == 429:
            limiter.slow_down()
            if backfill and domain in self.backfill_limiters:
                self.backfill_limiters[domain].slow_down()
        return FetchResult(url, str(r.url), r.status_code, r.text, elapsed=elapsed)
