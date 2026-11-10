"""Chạy toàn bộ luồng RSS -> tải -> trích xuất -> ghi file -> recheck với mạng giả lập."""

import asyncio
import gzip
import json
from datetime import timedelta

import httpx
from conftest import fixture_text

from crawler.app import Crawler
from crawler.timeutil import now, to_iso

VNE1 = "https://vnexpress.net/lua-bao-trum-quan-karaoke-co-nhieu-khach-o-dong-nai-5142197.html"
VNE2 = "https://vnexpress.net/bai-thu-hai-5142100.html"
TT1 = "https://tuoitre.vn/vu-cau-thu-miguel-qua-doi-100261108215707255.htm"
DT_OLD = "https://dantri.com.vn/thoi-su/hai-gio-chua-chay-quan-karaoke-hon-250m2-o-dong-nai-20261108212623865.htm"
DT_NEW = "https://dantri.com.vn/thoi-su/hon-hai-gio-chua-chay-quan-karaoke-250m2-o-dong-nai-20261108212623865.htm"


class FakeWeb:
    def __init__(self):
        self.pages = {
            "https://vnexpress.net/robots.txt": (200, "User-agent: *\nAllow: /\nUser-agent: Scrapy\nDisallow: /\n"),
            "https://tuoitre.vn/robots.txt": (200, "User-agent: *\nAllow: /\nDisallow: /print/\n"),
            "https://dantri.com.vn/robots.txt": (404, ""),
            "https://vnexpress.net/rss/tin-moi-nhat.rss": (200, fixture_text("vnexpress_rss.xml")),
            "https://tuoitre.vn/home.rss": (200, fixture_text("tuoitre_rss.xml")),
            "https://dantri.com.vn/rss/home.rss": (200, fixture_text("dantri_rss.xml")),
            VNE1: (200, fixture_text("vnexpress_article.html")),
            VNE2: (503, "busy"),
            TT1: (200, fixture_text("tuoitre_article.html")),
            DT_NEW: (200, fixture_text("dantri_article.html")),
        }
        self.redirects = {DT_OLD: DT_NEW}
        self.hits: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.hits.append(url)
        if url in self.redirects:
            return httpx.Response(301, headers={"Location": self.redirects[url]})
        status, body = self.pages.get(url, (404, "not found"))
        return httpx.Response(status, text=body, headers={"Content-Type": "text/html; charset=utf-8"})


def _records(data_dir):
    out = []
    for f in sorted(data_dir.rglob("*.jsonl.gz")):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            out.extend(json.loads(line) for line in fh)
    return out


def _fast(cfg):
    for s in cfg.sources.values():
        s.feeds = s.feeds[:1]  # chỉ feed đầu tiên của mỗi báo
        s.rate_limit = 1000
        s.backfill_rate = 1000


def test_once_end_to_end(cfg):
    _fast(cfg)
    web = FakeWeb()

    async def go():
        c = Crawler(cfg, transport=httpx.MockTransport(web.handler))
        try:
            done = await c.once(limit=10)
        finally:
            await c.close()
        return done

    done = asyncio.run(go())
    assert done["vnexpress"] >= 1 and done["tuoitre"] == 1 and done["dantri"] == 1

    recs = _records(cfg.data_dir)
    by_source = {r["source"]: r for r in recs}
    assert set(by_source) == {"vnexpress", "tuoitre", "dantri"}
    for r in recs:
        assert r["parse_ok"] and r["version_no"] == 1 and r["crawl_kind"] == "rss"
        assert r["html"] and r["content"] and r["published_at"].endswith("+07:00")
        assert len(r["article_id"]) == 40
    # Dân trí: URL cuối cùng sau chuyển hướng được lưu; article_id theo mã số không đổi.
    assert by_source["dantri"]["url"] == DT_NEW
    # Bài trỏ sang tên miền khác bị bỏ qua.
    assert not any("other.example.com" in h for h in web.hits)
    # Bài lỗi 503 nằm lại hàng đợi để thử lại, không bị ghi.
    assert all(r["url"] != VNE2 for r in recs)
    stats = json.loads(next((cfg.data_dir / "stats").glob("*.json")).read_text(encoding="utf-8"))
    assert stats["sources"]["vnexpress"]["written"] == 1
    assert stats["sources"]["vnexpress"]["http_5xx"] >= 1
    assert stats["files"]["records"] == len(recs)


def test_second_run_does_not_duplicate(cfg):
    _fast(cfg)
    web = FakeWeb()

    async def go():
        for _ in range(2):
            c = Crawler(cfg, transport=httpx.MockTransport(web.handler))
            try:
                await c.once(limit=10)
            finally:
                await c.close()

    asyncio.run(go())
    ids = [r["article_id"] for r in _records(cfg.data_dir)]
    assert len(ids) == len(set(ids)) == 3


def test_recheck_detects_edit_and_removal(cfg):
    _fast(cfg)
    web = FakeWeb()

    async def go():
        c = Crawler(cfg, transport=httpx.MockTransport(web.handler))
        try:
            await c.once(limit=10)
            st = c.state
            # Bài VnExpress bị sửa nội dung, bài Tuổi Trẻ bị gỡ.
            web.pages[VNE1] = (200, fixture_text("vnexpress_article.html").replace("khắc phục sự cố", "khắc phục hậu quả"))
            web.pages[TT1] = (404, "")
            past = to_iso(now() - timedelta(minutes=1))
            st.db.execute("UPDATE articles SET next_recheck_at=?", (past,))
            for a in st.due_rechecks(now()):
                st.enqueue(a.article_id, a.url, a.source, "recheck")
            for src in ("vnexpress", "tuoitre", "dantri"):
                while (t := st.claim(src, kinds=("recheck",))) is not None:
                    await c.process(t)
            return {a: st.get_article(a) for a in [r["article_id"] for r in _records(cfg.data_dir)]}
        finally:
            await c.close()

    asyncio.run(go())
    recs = _records(cfg.data_dir)
    vne = [r for r in recs if r["source"] == "vnexpress"]
    tt = [r for r in recs if r["source"] == "tuoitre"]
    dt = [r for r in recs if r["source"] == "dantri"]
    assert [r["version_no"] for r in vne] == [1, 2] and vne[1]["crawl_kind"] == "recheck"
    assert vne[0]["content_hash"] != vne[1]["content_hash"]
    assert [r["is_removed"] for r in tt] == [False, True]
    assert len(dt) == 1  # không đổi thì không ghi thêm


def test_run_loop_with_gap_and_backfill(cfg):
    """Chạy vòng lặp thật vài giây: RSS, worker, lấp khoảng trống và crawl ngược đều chạy được."""
    from datetime import date

    _fast(cfg)
    cfg.backfill.enabled = True
    cfg.backfill.until = date(2026, 5, 8)
    web = FakeWeb()
    today = now().date()
    # Sitemap giả cho Dân trí (tháng hiện tại) và Tuổi Trẻ.
    from crawler.discover.backfill import dantri_sitemap_url, tuoitre_sitemap_url, vnexpress_day_url

    old_dt = f"https://dantri.com.vn/xa-hoi/bai-cu-{today.strftime('%Y%m%d')}080000123.htm"
    web.pages[dantri_sitemap_url(today)] = (200, f"<urlset><url><loc>{old_dt}</loc></url></urlset>")
    web.pages[old_dt] = (200, fixture_text("dantri_article.html"))
    web.pages[tuoitre_sitemap_url(today)] = (200, f"<urlset><url><loc>{TT1}</loc></url></urlset>")
    web.pages[vnexpress_day_url(1001005, today)] = (200, fixture_text("vnexpress_archive.html"))

    async def go():
        c = Crawler(cfg, transport=httpx.MockTransport(web.handler))
        # Giả lập lần chạy trước dừng 3 giờ trước.
        c.state.set("heartbeat", to_iso(now() - timedelta(hours=3)))
        try:
            runner = asyncio.create_task(c.run())
            await asyncio.sleep(4)
            c.stop()
            await asyncio.wait_for(runner, 5)
        finally:
            await c.close()
        return c

    asyncio.run(go())
    recs = _records(cfg.data_dir)
    kinds = {(r["source"], r["crawl_kind"]) for r in recs}
    assert ("dantri", "gap") in kinds or ("dantri", "backfill") in kinds
    stats = json.loads(next((cfg.data_dir / "stats").glob("*.json")).read_text(encoding="utf-8"))
    assert stats["gaps"], "phải ghi nhận khoảng trống"
    # Trang lưu trữ VnExpress đã được đọc.
    assert any("/category/day/cateid/1001005/" in h for h in web.hits)


def test_domain_pause_keeps_tasks_and_recovers(cfg):
    """Dân trí không kết nối được: tạm dừng thay vì đốt hết lượt thử; kết nối lại thì
    đưa việc thất bại về hàng đợi và lên lịch lấp khoảng trống."""
    _fast(cfg)
    web = FakeWeb()
    down = {"dantri.com.vn": True}

    def handler(request):
        if down.get(request.url.host):
            raise httpx.ConnectTimeout("timeout", request=request)
        return web.handler(request)

    async def go():
        c = Crawler(cfg, transport=httpx.MockTransport(handler))
        try:
            st = c.state
            h = c.fetcher.health("dantri.com.vn")
            # Một bài Dân trí đang chờ trong hàng đợi.
            from crawler.normalize import make_article_id

            aid = make_article_id("dantri", DT_NEW, r"-(\d+)\.htm$")
            st.enqueue(aid, DT_NEW, "dantri", "rss")
            # 5 lần lỗi liên tiếp -> tạm dừng.
            for _ in range(5):
                await c.fetcher.fetch("https://dantri.com.vn/rss/home.rss")
            assert c.fetcher.paused_for("dantri.com.vn") > 0
            hits_before = len(web.hits)
            task = st.claim("dantri")
            await c.process(task)  # đang tạm dừng: không gửi request, không tính lượt thử
            assert len(web.hits) == hits_before
            row = st.db.execute("SELECT status, attempts FROM frontier WHERE article_id=?", (aid,)).fetchone()
            assert tuple(row) == ("pending", 0)
            # Một việc khác đã thất bại vì lỗi mạng trước đó.
            st.enqueue("x" * 40, "https://dantri.com.vn/a-20261110000000000.htm", "dantri", "rss")
            t2 = st.claim("dantri", kinds=("rss",), at=now() + timedelta(days=1))
            st.fail(t2.id, "ConnectTimeout: timeout")
            # Trang hoạt động lại sau 2 giờ.
            down["dantri.com.vn"] = False
            h.paused_until = 0
            h.down_since -= 2 * 3600
            res = await c.fetcher.fetch("https://dantri.com.vn/rss/home.rss")
            assert res.ok and h.recovered_after >= 2 * 3600
            c._check_recoveries()
            failed = st.db.execute("SELECT COUNT(*) FROM frontier WHERE status='failed'").fetchone()[0]
            assert failed == 0
            assert st.get("gap_days:dantri")
        finally:
            await c.close()

    asyncio.run(go())
    stats = json.loads(next((cfg.data_dir / "stats").glob("*.json")).read_text(encoding="utf-8"))
    assert any(g.get("source") == "dantri" for g in stats["gaps"])


def test_category_feeds_polled_less_often(cfg):
    _fast(cfg)
    for s in cfg.sources.values():
        s.feeds = [s.feeds[0], s.feeds[0] + "?cat=1", s.feeds[0] + "?cat=2"]
    c = Crawler(cfg, transport=httpx.MockTransport(FakeWeb().handler))
    try:
        src = c.sources["vnexpress"]
        assert len(c._feeds_due(src)) == 3  # lần đầu: đọc hết
        import time as _t

        for f in src.feeds:
            c._feed_polled[f] = _t.monotonic() - cfg.rss_interval_seconds
        assert c._feeds_due(src) == [src.feeds[0]]  # 5 phút sau: chỉ feed tin mới
    finally:
        asyncio.run(c.close())
