#!/usr/bin/env bash
# Chạy liên tục việc đẩy dữ liệu lên Hugging Face trong phiên tmux "offload".
#
#   bash scripts/offload.sh <tên>/news-crawler-data      # bắt đầu (mặc định 6 giờ một lần)
#   bash scripts/offload.sh <tên>/news-crawler-data 3    # 3 giờ một lần
#   tmux attach -t offload                                # xem; Ctrl+B rồi D để thoát mà vẫn chạy
#   tmux kill-session -t offload                          # dừng
#
# Token quyền Write ghi vào file .hf_token ở thư mục repo (đã có trong .gitignore):
#   echo hf_xxx > .hf_token && chmod 600 .hf_token
set -euo pipefail

REPO="${1:?Thiếu tên dataset, ví dụ: bash scripts/offload.sh VietCH57/news-crawler-data}"
HOURS="${2:-6}"
cd "$(dirname "$0")/.."

if [ ! -f .hf_token ] && [ -z "${HF_TOKEN:-}" ]; then
  echo "Chưa có token. Chạy: echo hf_xxx > .hf_token && chmod 600 .hf_token" >&2
  exit 1
fi
if tmux has-session -t offload 2>/dev/null; then
  echo "Phiên tmux 'offload' đang chạy. Xem: tmux attach -t offload" >&2
  exit 1
fi

tmux new -d -s offload ".venv/bin/python tools/offload_hf.py --repo '$REPO' --every-hours $HOURS"
echo "Đã chạy trong tmux 'offload' ($HOURS giờ một lần). Log: data/logs/offload.log"
