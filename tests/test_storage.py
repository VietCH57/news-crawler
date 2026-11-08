import gzip
import json
from datetime import timedelta

from crawler.sinks import FileSink
from crawler.state import State
from crawler.timeutil import now, to_iso


def _read_all(root):
    out = []
    for f in sorted(root.rglob("*.jsonl.gz")):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            out.extend(json.loads(line) for line in fh)
    return out


def test_sink_rotates_by_hour_and_compresses(tmp_path):
    sink = FileSink(tmp_path)
    sink.write({"article_id": "a", "crawled_at": "2026-11-09T08:10:00+07:00", "title": "Tiếng Việt"})
    sink.write({"article_id": "b", "crawled_at": "2026-11-09T08:50:00+07:00"})
    sink.write({"article_id": "c", "crawled_at": "2026-11-09T09:01:00+07:00"})
    sink.close()
    files = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.jsonl.gz"))
    assert len(files) == 2
    assert files[0].startswith("landing/articles/dt=2026-11-09/hour=08/")
    assert files[1].startswith("landing/articles/dt=2026-11-09/hour=09/")
    assert not list(tmp_path.rglob("*.tmp"))
    recs = _read_all(tmp_path)
    assert [r["article_id"] for r in recs] == ["a", "b", "c"]
    assert recs[0]["title"] == "Tiếng Việt"


def test_sink_recovers_leftover_tmp(tmp_path):
    sink = FileSink(tmp_path)
    sink.write({"article_id": "a", "crawled_at": "2026-11-09T08:10:00+07:00"})
    # Mô phỏng máy tắt đột ngột: không gọi close(), file .tmp còn lại.
    sink._fh.close()
    sink._fh = None
    assert list(tmp_path.rglob("*.jsonl.tmp"))
    FileSink(tmp_path)  # khởi động lại
    assert not list(tmp_path.rglob("*.jsonl.tmp"))
    assert [r["article_id"] for r in _read_all(tmp_path)] == ["a"]


def test_state_queue_dedup_priority_retry(tmp_path):
    st = State(tmp_path / "s.db")
    assert st.enqueue("a1", "u1", "vne", "backfill")
    assert st.enqueue("a2", "u2", "vne", "rss")
    assert not st.enqueue("a2", "u2", "vne", "rss")  # trùng thì bỏ qua
    t = st.claim("vne")
    assert t.article_id == "a2"  # rss được ưu tiên hơn backfill
    st.retry(t.id, 3600, "HTTP 503")
    t2 = st.claim("vne")
    assert t2.article_id == "a1"  # a2 chưa đến hạn thử lại
    st.done(t2.id)
    assert st.claim("vne") is None
    assert st.pending_count("vne") == 1


def test_state_inflight_reset_on_restart(tmp_path):
    st = State(tmp_path / "s.db")
    st.enqueue("a1", "u1", "vne", "rss")
    assert st.claim("vne") is not None
    st.close()
    st2 = State(tmp_path / "s.db")
    assert st2.claim("vne").article_id == "a1"


def test_state_due_rechecks(tmp_path):
    st = State(tmp_path / "s.db")
    t = now()
    st.add_article("a1", "vne", "u1", "h", to_iso(t), to_iso(t - timedelta(minutes=1)))
    st.add_article("a2", "vne", "u2", "h", to_iso(t), to_iso(t + timedelta(hours=1)))
    st.add_article("a3", "vne", "u3", "h", to_iso(t), None)
    assert [a.article_id for a in st.due_rechecks(t)] == ["a1"]
    st.set("k", {"x": [1, 2]})
    assert st.get("k") == {"x": [1, 2]} and st.get("missing", 5) == 5
