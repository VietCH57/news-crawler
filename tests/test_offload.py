import importlib.util
import os
import time
from pathlib import Path

_spec = importlib.util.spec_from_file_location("offload_hf", Path(__file__).resolve().parents[1] / "tools" / "offload_hf.py")
offload = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(offload)


def test_only_old_closed_gz_files_selected(tmp_path):
    landing = tmp_path / "landing"
    d = landing / "articles" / "dt=2026-11-11" / "hour=08"
    d.mkdir(parents=True)
    old_gz, new_gz, tmp = d / "part-a.jsonl.gz", d / "part-b.jsonl.gz", d / "part-c.jsonl.tmp"
    for p in (old_gz, new_gz, tmp):
        p.write_bytes(b"x")
    t = time.time()
    os.utime(old_gz, (t - 30 * 3600, t - 30 * 3600))
    os.utime(tmp, (t - 30 * 3600, t - 30 * 3600))
    assert offload.closed_files(landing, keep_hours=24, now=t) == [old_gz]

    old_gz.unlink(); new_gz.unlink(); tmp.unlink()
    offload.remove_empty_dirs(landing)
    assert not (landing / "articles" / "dt=2026-11-11").exists()
