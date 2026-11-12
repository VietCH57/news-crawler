# news-crawler

Crawler báo điện tử Việt Nam cho bài tập lớn môn Lưu trữ và xử lý dữ liệu lớn: thu thập bài viết từ RSS (bài mới) và sitemap / trang lưu trữ (bài cũ), ghi ra file JSON Lines nén theo giờ, sẵn sàng nạp vào Kafka và HDFS.

10 báo đang chạy, 2 báo tạm tắt (khảo sát ngày 8 và 10/11/2026). Không thu thập bình luận.

| Báo | Nguồn bài mới | Bài cũ (crawl ngược, lấp khoảng trống) | Tốc độ |
|---|---|---|---|
| VnExpress | RSS | Trang lưu trữ theo ngày của 9 chuyên mục | 1 req/s |
| Tuổi Trẻ | RSS | Sitemap 5 ngày | 1 req/s |
| Thanh Niên | RSS | Sitemap 5 ngày | 0,5 req/s |
| VietnamNet | Google News sitemap (RSS nặng 1 MB) | Sitemap tháng, lọc theo lastmod | 0,5 req/s |
| Nhân Dân | RSS | Sitemap tháng, lọc theo lastmod | 0,5 req/s |
| VietnamPlus (TTXVN) | RSS | Sitemap tháng, lọc theo lastmod | 0,5 req/s |
| Tiền Phong | RSS | Sitemap tháng, lọc theo lastmod | 0,5 req/s |
| VTC News | RSS | Sitemap theo ngày | 0,5 req/s |
| Sài Gòn Giải Phóng | RSS | Sitemap tháng, lọc theo lastmod | 0,5 req/s |
| VOV | RSS (đọc mỗi 15 phút) | Chưa hỗ trợ (sitemap không chia theo ngày) | 1 request/20 giây theo Crawl-delay của robots.txt |
| Lao Động | RSS | Chưa hỗ trợ | **Đang tắt**: server chỉ nhận 177 byte, nghi trang chống bot |
| Dân trí | RSS | Sitemap tháng | **Đang tắt**: đã chặn IP server ngày 9/11/2026 |

Crawler tự tuân theo `Crawl-delay` trong robots.txt. Báo mới thêm (chưa có bài nào trong `data/state/crawler.db`) được **khởi động nhẹ**: lần đọc RSS đầu chỉ lấy bài của 3 giờ gần nhất (`warm_start_hours`), không tải dồn hàng trăm bài.

## Cài đặt

Cần Python 3.11 trở lên.

**Windows 11** (PowerShell):

```powershell
git clone https://github.com/VietCH57/news-crawler.git
cd news-crawler
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

**Ubuntu**:

```bash
git clone https://github.com/VietCH57/news-crawler.git
cd news-crawler
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Chạy thử lần đầu

Code chưa được chạy trên trang báo thật, chỉ chạy trên trang mẫu mô phỏng. Nên thử theo thứ tự:

```bash
# 1. Test offline
pytest

# 2. Thử trích xuất một bài của mỗi báo (không ghi file). Lấy URL bất kỳ trên trang báo.
python -m crawler parse https://vnexpress.net/<bai-viet>-<maso>.html
python -m crawler parse https://tuoitre.vn/<bai-viet>-<maso>.htm
python -m crawler parse https://dantri.com.vn/<chuyen-muc>/<bai-viet>-<maso>.htm

# 3. Đọc RSS một lần, tải tối đa 5 bài mỗi báo
python -m crawler once --limit 5

# 4. Chạy liên tục (Ctrl+C để dừng an toàn)
python -m crawler run
```

Ở bước 2, kiểm tra `parse_ok: true`, `title`, `sapo`, `published_at`, `author` và `content_preview`. Nếu `used_fallback: true` thì selector của báo đó cần sửa (xem phần *Khi báo đổi giao diện*).

## Dữ liệu đầu ra

```text
data/
├── landing/articles/dt=2026-11-09/hour=08/part-<máy>-<giờ>-<pid>.jsonl.gz
├── state/crawler.db        SQLite: hàng đợi, bài đã thấy, mốc crawl ngược
├── stats/2026-11-09.json   thống kê ngày theo từng báo
└── logs/crawler.log
```

- Mỗi dòng trong file `.jsonl.gz` là một bản tin. Các trường gồm: `article_id`, `url`, `source`, `crawl_kind`, `version_no`, `content_hash`, `is_removed`, `title`, `sapo`, `content`, `author`, `category_raw`, `tags`, `published_at`, `crawled_at`, `http_status`, `parse_ok`, `extract_method`, `word_count`, `is_short`, `media_type`, `parser_version`, `html`.
- `extract_method`: `selector` (bài thường), `selector+captions` (bài ảnh: thêm chú thích ảnh), `trafilatura` (bố cục lạ, cần xem lại). `is_short: true` là bài có ít chữ; nên lọc riêng khi phân tích từ khóa. Với bài ít chữ, `media_type` cho biết loại trang: `photo` (kể cả bài chỉ có ảnh: infographic, kết quả xổ số), `video`, `audio` (podcast, bản tin nghe), `interactive` (đồ họa tương tác nạp bằng JavaScript, crawler không lấy được chữ), `quiz` (câu đố VnExpress), hoặc `text`. Bài đủ chữ luôn có `media_type: text`, kể cả podcast có lời thoại. Trang ảnh/video/podcast được coi là trích xuất đạt dù không có chữ; tiêu đề và sapo vẫn có. Các trường này có từ ngày 10/11/2026; bản tin trước đó không có.
- `dt`/`hour` là **giờ thu thập**, cùng cấu trúc với vùng landing trên HDFS.
- `article_id` = SHA-1 của `báo:mã số bài`. Mã số này nằm ở cuối URL và không đổi khi báo sửa tiêu đề.
- Mỗi bài mới được tải lại sau 1, 6 và 24 giờ. Nếu nội dung đổi, crawler ghi thêm bản tin với `version_no` tăng lên. Nếu bài bị gỡ (404/410, hoặc báo chuyển hướng về trang chủ hay `/404.html`), crawler ghi bản tin `is_removed: true`.
- Khi máy tắt quá 30 phút, lần chạy sau tự lấy bài của những ngày bị lỡ (`crawl_kind: gap`).

Xem nhanh dữ liệu:

```bash
python -m crawler stats
python -c "import gzip,json,glob; f=sorted(glob.glob('data/landing/articles/*/*/*.jsonl.gz'))[-1]; r=json.loads(gzip.open(f,'rt',encoding='utf-8').readline()); r.pop('html'); print(json.dumps(r,ensure_ascii=False,indent=2))"
```

## Crawl ngược 6 tháng

Mặc định đang tắt. Khi phần lấy bài mới đã chạy ổn, mở `config.yaml`:

```yaml
backfill:
  enabled: true
  until: 2026-05-08
```

VnExpress dùng trang lưu trữ theo ngày của 9 chuyên mục trong `config.yaml` (đã kiểm tra ngày 12/11/2026). Nếu trang này không còn dùng được, đổi sang cách dự phòng `vnexpress_category_pages` (ghi sẵn trong `config.yaml`, chỉ lùi được vài tuần).

## Chạy lâu dài trên Ubuntu

Dùng `deploy/news-crawler.service`; hướng dẫn nằm ở đầu file. Trên Windows có thể để chạy trong Windows Terminal, và nhớ tắt chế độ ngủ khi máy đang cắm sạc.

## Kiểm tra nhanh

```bash
python tools/check_sources.py sggp vov          # đọc feed + trích xuất thử một bài
python tools/review_extraction.py --summary     # đếm bài theo cách trích xuất, trên dữ liệu đã lưu
python tools/review_extraction.py tienphong     # liệt kê bài ảnh/video/bố cục lạ của một báo
python tools/review_extraction.py --errors      # chỉ bài lỗi và bài dùng trafilatura
```

## Đẩy dữ liệu khỏi server

`tools/offload_hf.py` đẩy các file `.jsonl.gz` đã đóng (cũ hơn 24 giờ) lên một dataset **riêng tư** trên Hugging Face (tài khoản miễn phí có 100 GB riêng tư), kiểm tra kích thước rồi mới xóa bản trên server. Không đụng tới `data/state`.

```bash
export HF_TOKEN=hf_xxx      # token quyền Write
python tools/offload_hf.py --repo <tên>/news-crawler-data --dry-run
python tools/offload_hf.py --repo <tên>/news-crawler-data
```

## Khi báo đổi giao diện

1. Lưu một trang mẫu: `python tools/save_fixture.py <url>`. Trang được lưu vào `tests/fixtures/real/`; thư mục này không đưa lên GitHub.
2. Thử: `python -m crawler parse tests/fixtures/real/<file>.html --source <báo>`
3. Sửa selector trong `crawler/parsers/sites.py`, rồi tăng `version` của parser đó.

## Cấu trúc code

```text
crawler/
├── __main__.py        dòng lệnh: run, once, parse, stats
├── app.py             vòng RSS, worker theo báo, recheck, crawl ngược, nhịp tim
├── config.py          đọc config.yaml
├── normalize.py       chuẩn hóa URL, article_id, content_hash
├── state.py           SQLite: frontier, articles, kv
├── fetcher.py         httpx, robots.txt, giới hạn tốc độ theo báo
├── sinks.py           FileSink (sau này thêm KafkaSink)
├── stats.py           thống kê ngày
├── timeutil.py        đọc ngày giờ nhiều định dạng, múi giờ +07:00
├── discover/
│   ├── rss.py
│   └── backfill.py    sitemap Tuổi Trẻ, Dân trí; trang lưu trữ VnExpress
└── parsers/
    ├── base.py        JSON-LD, thẻ meta, dự phòng trafilatura
    └── sites.py       selector riêng từng báo
```

## Nguyên tắc

- Tuân theo robots.txt.
- Mỗi báo tối đa 1 request/giây (crawl ngược 0,5 request/giây).
- User-Agent ghi tên dự án và link repo.
- Dữ liệu chỉ dùng cho mục đích học tập, không công bố lại toàn văn bài báo.
