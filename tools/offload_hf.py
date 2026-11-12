"""Đẩy file đã đóng ở vùng landing lên một dataset riêng tư trên Hugging Face, rồi xóa bản trên server.

    # Token quyền Write (huggingface.co/settings/tokens): biến HF_TOKEN hoặc file .hf_token
    python tools/offload_hf.py --repo <tên>/news-crawler-data --dry-run   # xem sẽ làm gì
    python tools/offload_hf.py --repo <tên>/news-crawler-data             # chạy một lần
    python tools/offload_hf.py --repo <tên>/news-crawler-data --every-hours 6   # chạy mãi, 6 giờ một lần

Chỉ đụng tới file ``data/landing/articles/dt=*/hour=*/*.jsonl.gz`` đã đóng (crawler không ghi
thêm vào file .gz) và cũ hơn ``--keep-hours`` (mặc định 24 giờ, để ``review_extraction.py``
vẫn xem được dữ liệu gần đây). File chỉ bị xóa khi bản trên Hugging Face có cùng kích thước.
Không đụng tới ``data/state`` (crawler cần ``crawler.db`` để biết bài nào đã có).

Dataset luôn tạo ở chế độ riêng tư: đây là toàn văn bài báo, không được công bố lại.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crawler.config import load_config  # noqa: E402

log = logging.getLogger("offload")
BATCH = 200  # số file mỗi lần commit (Hugging Face khuyên không commit quá nhiều file một lúc)


def closed_files(landing: Path, keep_hours: float, now: float | None = None) -> list[Path]:
    """File .jsonl.gz đã đóng, sửa lần cuối cách đây hơn ``keep_hours`` giờ."""
    now = time.time() if now is None else now
    limit = now - keep_hours * 3600
    files = [p for p in landing.glob("articles/dt=*/hour=*/*.jsonl.gz") if p.stat().st_mtime < limit]
    return sorted(files)


def remove_empty_dirs(landing: Path) -> None:
    for d in sorted(landing.glob("articles/dt=*/hour=*"), reverse=True) + sorted(landing.glob("articles/dt=*"), reverse=True):
        try:
            d.rmdir()  # chỉ xóa được thư mục rỗng
        except OSError:
            pass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="ví dụ VietCH57/news-crawler-data")
    ap.add_argument("--keep-hours", type=float, default=24, help="giữ lại trên server các file mới hơn (giờ)")
    ap.add_argument("--dry-run", action="store_true", help="chỉ in ra, không đẩy, không xóa")
    ap.add_argument("--no-delete", action="store_true", help="đẩy lên nhưng không xóa bản trên server")
    ap.add_argument("--every-hours", type=float, default=0, help="chạy lặp lại mãi, cách nhau chừng này giờ")
    args = ap.parse_args()

    cfg = load_config(ROOT / "config.yaml")
    landing = cfg.data_dir / "landing"
    (cfg.data_dir / "logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(cfg.data_dir / "logs" / "offload.log", encoding="utf-8")],
    )

    token_file = ROOT / ".hf_token"
    if not os.environ.get("HF_TOKEN") and token_file.exists():
        os.environ["HF_TOKEN"] = token_file.read_text(encoding="utf-8").strip()

    if args.every_hours <= 0:
        run_once(args, landing)
        return
    log.info("Chạy liên tục, %g giờ một lần. Ctrl+C để dừng.", args.every_hours)
    while True:
        try:
            run_once(args, landing)
        except KeyboardInterrupt:
            raise
        except Exception:  # noqa: BLE001  # mất mạng, Hugging Face lỗi: lần sau thử lại
            log.exception("Lượt này lỗi, sẽ thử lại ở lượt sau")
        try:
            time.sleep(args.every_hours * 3600)
        except KeyboardInterrupt:
            log.info("Dừng.")
            return


def run_once(args: argparse.Namespace, landing: Path) -> None:
    files = closed_files(landing, args.keep_hours)
    total = sum(p.stat().st_size for p in files)
    log.info("%d file đủ điều kiện, %.1f MB", len(files), total / 1e6)
    if not files or args.dry_run:
        for p in files[:10]:
            log.info("  %s", p.relative_to(landing))
        return

    if not os.environ.get("HF_TOKEN"):
        raise SystemExit("Chưa có token: đặt HF_TOKEN hoặc ghi token vào file .hf_token ở thư mục repo")
    from huggingface_hub import HfApi

    api = HfApi()
    api.create_repo(args.repo, repo_type="dataset", private=True, exist_ok=True)

    rel = [p.relative_to(landing).as_posix() for p in files]
    for i in range(0, len(rel), BATCH):
        batch = rel[i:i + BATCH]
        api.upload_folder(
            repo_id=args.repo,
            repo_type="dataset",
            folder_path=landing,
            path_in_repo="landing",
            allow_patterns=batch,
            commit_message=f"landing: {batch[0].split('/')[1]} .. {batch[-1].split('/')[1]} ({len(batch)} file)",
        )
        log.info("Đã đẩy %d/%d file", min(i + BATCH, len(rel)), len(rel))

    if args.no_delete:
        return
    deleted = freed = 0
    for i in range(0, len(rel), BATCH):
        batch = rel[i:i + BATCH]
        remote = {
            f.path: getattr(f, "size", None)
            for f in api.get_paths_info(args.repo, [f"landing/{r}" for r in batch], repo_type="dataset")
        }
        for r in batch:
            local = landing / r
            size = local.stat().st_size
            if remote.get(f"landing/{r}") == size:
                local.unlink()
                deleted += 1
                freed += size
            else:
                log.warning("Chưa xóa %s: bản trên Hugging Face thiếu hoặc khác kích thước", r)
    remove_empty_dirs(landing)
    log.info("Đã xóa %d file trên server, giải phóng %.1f MB", deleted, freed / 1e6)


if __name__ == "__main__":
    main()
