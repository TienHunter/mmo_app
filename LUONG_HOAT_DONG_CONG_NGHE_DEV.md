# MMO App — Luồng hoạt động công nghệ dành cho dev

Tài liệu này mô tả kiến trúc kỹ thuật, luồng xử lý và các điểm cần lưu ý khi bảo trì/phát triển MMO App.

Mục tiêu sản phẩm: người dùng chỉ mở một ứng dụng desktop duy nhất `MMOApp.exe`, nhưng bên trong app quản lý trọn flow:

```text
YouTube WebSub/RSS → download video → process video → upload TikTok
```

Các project cũ (`youtube_tracker`, `video_processor`, `tiktok_auto_upload`) không còn chạy như app độc lập ở phía người dùng. Năng lực của chúng được gom vào `D:\mmo\mmo_app` dưới dạng service/module nội bộ.

---

## 1. Entry point và composition root

### Entry point

```text
D:\mmo\mmo_app\main.py
```

`main.py` chỉ nên làm nhiệm vụ khởi động app desktop.

### Composition root

```text
D:\mmo\mmo_app\mmo_app\application.py
```

`Application` là nơi nối các thành phần chính:

```text
Settings
  ↓
Database
  ↓
YouTubeService
WebSubService
PipelineOrchestrator
Desktop UI
```

`Application.start()` chịu trách nhiệm:

- khởi động WebSub nếu tracking mode là `websub` hoặc `both`;
- subscribe/renew WebSub;
- khởi động vòng RSS fallback;
- khởi động pipeline worker.

Lưu ý UX/lifecycle:

```text
Mở app không được tự start.
```

App chỉ được bắt đầu chạy khi người dùng đã nhập/lưu cấu hình và bấm `Bắt đầu`. `Desktop.__init__` không gọi `Application.start()`. Việc lưu cấu hình cũng không được tự bật WebSub/RSS nếu runtime chưa started.

`Application.pause()` tạm dừng runtime: dừng WebSub server, dừng vòng RSS fallback và pause pipeline worker nhưng không xóa queue.

`Application.shutdown()` dừng WebSub, pipeline và các worker liên quan theo thứ tự an toàn.

---

## 2. Cấu trúc thư mục quan trọng

```text
D:\mmo\mmo_app
  main.py
  build_windows.ps1
  mmo_app.spec
  README.md
  HUONG_DAN_SU_DUNG.md
  LUONG_HOAT_DONG_CONG_NGHE_DEV.md

  mmo_app\
    application.py
    database.py
    desktop.py
    migration.py
    schema.sql
    services.py
    settings.py
    websub.py

  tests\
    test_pipeline.py

  dist\
    MMOApp\
      MMOApp.exe
    MMOApp-Windows-x64.zip
```

Nguyên tắc release hiện tại:

```text
Chỉ build vào D:\mmo\mmo_app\dist
Chỉ phát hành MMOApp.exe / MMOApp-Windows-x64.zip
Không phát hành EXE riêng của các project cũ
```

---

## 3. Settings và dữ liệu runtime

### File cấu hình

Code chính:

```text
mmo_app\settings.py
```

`Settings` quản lý:

- tracking mode: `both`, `websub`, `rss`;
- WebSub bind host/port/public domain;
- RSS fallback interval;
- thư mục download/processed;
- TikTok CDP URL mặc định;
- cờ `tiktok_publish_enabled`;
- tỷ lệ video đầu ra;
- overlay title/part: bật/tắt, font, màu, cỡ chữ;
- đường dẫn FFmpeg/FFprobe bundled.

### App data

Theo thiết kế, dữ liệu mutable nên nằm dưới:

```text
%LOCALAPPDATA%\MMOApp
```

Các đường dẫn media có thể do người dùng cấu hình, nhưng database/config/log phải dùng chung cho app thống nhất.

---

## 4. Database và queue chung

Code chính:

```text
mmo_app\database.py
mmo_app\schema.sql
```

Schema hiện tại dùng SQLite, version:

```text
SCHEMA_VERSION = 4
```

Các bảng chính:

| Bảng | Vai trò |
|---|---|
| `channels` | Kênh YouTube đã cấu hình/theo dõi |
| `media_items` | Video YouTube duy nhất trong hệ thống |
| `artifacts` | File sinh ra trong pipeline: download/rendered/... |
| `jobs` | Hàng đợi chung cho `download`, `process`, `upload` |
| `job_events` | Log/audit theo job |
| `tiktok_accounts` | Kênh/account TikTok đích |
| `channel_routes` | Một layout mapping YouTube sources → TikTok account |
| `channel_route_sources` | Danh sách kênh YouTube thuộc một route/layout |
| `upload_tasks` | Nhiệm vụ upload từng rendered artifact lên TikTok |

### Trạng thái job

Các trạng thái dùng trong queue:

```text
queued
leased
retry_wait
manual_action
completed
failed
```

Luồng claim job:

```text
Database.claim(worker, kinds)
  → lấy job queued/retry_wait đã đến available_at
  → set status = leased
  → gắn lease_owner + lease_expires_at
```

Khi app restart:

```text
Database.recover_expired_leases()
```

đưa lease hết hạn về `queued` để chạy lại.

### Idempotency

Queue dùng `idempotency_key` để tránh tạo job trùng.

Ví dụ:

```text
download:<youtube_video_id>
process:<media_item_id>:<download_artifact_id>
upload:<upload_task_id>
```

Khi sửa pipeline, cần giữ nguyên tinh thần: mỗi stage có thể chạy lại an toàn.

---

## 5. Luồng tổng thể

```text
User config layout
  ↓
Resolve YouTube URL → channel_id
  ↓
Save channels + routes
  ↓
Start app
  ↓
WebSub primary / RSS fallback discovers video
  ↓
media_items + download job
  ↓
DownloadHandler downloads source video
  ↓
artifacts(kind='download') + process job
  ↓
ProcessHandler renders video/segments
  ↓
artifacts(kind='rendered') + upload_tasks + upload jobs
  ↓
UploadHandler uploads to TikTok with caption
  ↓
media_items.status = uploaded
```

---

## 6. Layout routing: nhiều YouTube → một TikTok

UI chính:

```text
mmo_app\desktop.py
```

Tab:

```text
Luồng YouTube → TikTok
```

Người dùng tạo nhiều layout. Mỗi layout gồm:

```text
name
TikTok account name
TikTok Chrome CDP URL
list of YouTube channel URLs
```

Khi lưu layout:

1. App chuẩn hóa URL YouTube.
2. App resolve URL sang `channel_id`.
3. Ghi/merge vào `channels`.
4. Ghi TikTok account vào `tiktok_accounts`.
5. Ghi route vào `channel_routes`.
6. Ghi source mapping vào `channel_route_sources`.

Ví dụ logic mapping:

```text
channel_route_sources.channel_id = UC...
  ↓
channel_routes.account_id
  ↓
tiktok_accounts.id
```

Khi process xong video, `_route_uploads()` sẽ tìm account TikTok dựa trên `media_items.channel_id`.

Nếu không tìm thấy route, app fallback về TikTok account active đầu tiên. Khi phát triển tiếp, có thể đổi fallback này thành trạng thái `waiting_route` nếu muốn an toàn hơn.

---

## 7. Resolve YouTube URL sang channel_id

Code chính:

```text
mmo_app\services.py
class YouTubeService
```

Hàm quan trọng:

```text
resolve_channel_url(value)
resolve_channel_urls(values)
normalize_channel_url(value)
sync_channels(resolved)
```

Yêu cầu UX hiện tại:

```text
Người dùng chỉ copy URL kênh YouTube, app tự lấy channel_id.
```

Dạng URL phổ biến:

```text
https://www.youtube.com/@tien5140
https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx
```

Nếu URL là `/channel/UC...`, app có thể lấy channel ID trực tiếp.

Nếu URL dạng handle `/@name`, app cần truy cập metadata/trang YouTube để resolve ra `channel_id`.

---

## 8. WebSub là primary, RSS là fallback

Code chính:

```text
mmo_app\websub.py
mmo_app\application.py
mmo_app\services.py
```

Tracking mode:

```text
both    = WebSub primary + RSS fallback
websub  = chỉ WebSub
rss     = chỉ RSS
```

Mặc định định hướng sản phẩm:

```text
WebSub là chính, RSS chỉ fallback.
```

### WebSub flow

```text
Application.start()
  ↓
WebSubService.start()
  ↓
HTTP server listen http://<webhook_host>:<webhook_port>/webhook
  ↓
WebSubService.renew_due(force=True)
  ↓
subscribe YouTube Hub
  ↓
YouTube verify callback GET /webhook
  ↓
YouTube sends notification POST /webhook
  ↓
WebSubService.receive_notification()
  ↓
YouTubeService.discover()
  ↓
enqueue download job
```

### Ý nghĩa `webhook_host`

`webhook_host` chỉ là bind host nội bộ của HTTP server.

Khuyến nghị:

```text
127.0.0.1
```

Khi dùng Cloudflare Tunnel hoặc reverse proxy cùng máy:

```text
Public HTTPS domain → tunnel/proxy → http://127.0.0.1:8000
```

Không nhầm `webhook_host` với public callback URL. Public callback được tạo từ:

```text
settings.public_domain + "/webhook"
```

### RSS fallback flow

```text
Application._rss_loop()
  ↓
YouTubeService.poll_rss()
  ↓
fetch https://www.youtube.com/feeds/videos.xml?channel_id=...
  ↓
YouTubeService.discover()
  ↓
enqueue download job
```

RSS fallback chạy theo:

```text
settings.rss_interval_seconds
```

---

## 9. Discovery và download

Code chính:

```text
mmo_app\services.py
YouTubeService
DownloadHandler
```

### Discovery

`YouTubeService.discover(video_id, channel_id, title, published_at)`:

1. Kiểm tra `media_items.youtube_video_id`.
2. Nếu chưa có, insert `media_items`.
3. Enqueue job:

   ```text
   kind = download
   idempotency_key = download:<video_id>
   ```

### Download

`DownloadHandler.run(job)`:

1. Load `media_items`.
2. Dùng yt-dlp tải video.
3. Kiểm tra file tồn tại và size > 0.
4. Insert artifact:

   ```text
   artifacts.kind = download
   ```

5. Update:

   ```text
   media_items.status = downloaded
   ```

6. Enqueue process job:

   ```text
   kind = process
   payload = {"artifact_id": <download_artifact_id>}
   ```

---

## 10. Process video

Code chính:

```text
mmo_app\services.py
ProcessHandler
```

### Input

Process job nhận:

```json
{"artifact_id": 123}
```

Artifact input:

```text
artifacts.kind = download
```

### Output folder

Video processed được route vào folder của kênh TikTok tương ứng.

Ví dụ:

```text
processed\TikTok Kien Thuc\<YouTube video ID>\001.mp4
processed\TikTok Kien Thuc\<YouTube video ID>\002.mp4
```

Tên folder TikTok được lấy từ mapping:

```text
media_items.channel_id
  ↓
channel_route_sources
  ↓
channel_routes
  ↓
tiktok_accounts.name
```

Nếu không có mapping, fallback về `channel_id`.

### Tỷ lệ video

Option:

```text
original
9:16
4:5
1:1
16:9
```

Khi không phải `original`, FFmpeg filter dùng scale + pad nền đen, không crop:

```text
scale=<w>:<h>:force_original_aspect_ratio=decrease,
pad=<w>:<h>:(ow-iw)/2:(oh-ih)/2:black
```

Mục tiêu: giữ toàn bộ hình gốc trong canvas đích, không kéo méo video; phần dư là nền đen để có không gian chèn title/part.

### Overlay title/part

Settings:

```text
overlay_text_enabled
overlay_font_path
overlay_font_color
overlay_font_size
```

Khi bật:

- title YouTube được chèn ở phía trên;
- nếu có nhiều segment, phía dưới chèn `Part 1`, `Part 2`, ...
- text tiếng Việt được ghi qua file UTF-8 tạm, rồi FFmpeg `drawtext` đọc bằng `textfile`.

Segment splitting dùng `ProcessHandler.segment_ranges(duration, target_seconds)`. Nếu segment cuối ngắn hơn `target_seconds / 2`, segment cuối được merge vào segment trước để tránh tạo một file cuối quá ngắn.

Lý do dùng `textfile`: tránh lỗi escape ký tự đặc biệt, dấu tiếng Việt và path Windows.

### Rendered artifacts

Mỗi file output được ghi:

```text
artifacts.kind = rendered
artifacts.segment_index = 1, 2, 3...
```

Sau đó `_route_uploads()` tạo upload tasks.

---

## 11. Caption và upload task

Caption không lấy từ tên file.

`tiktok_accounts.daily_limit` là giới hạn nội bộ theo từng tài khoản TikTok. `0` nghĩa là không giới hạn. `UploadHandler` reset `posts_today` khi `counter_date` khác ngày hiện tại trước khi kiểm tra giới hạn.

Nguồn caption:

```text
media_items.title
```

Khi tạo upload task:

```text
upload_tasks.caption = media_items.title
```

Nếu video có nhiều part:

```text
<YouTube title> - Part 1
<YouTube title> - Part 2
<YouTube title> - Part 3
```

Ví dụ:

```text
Title YouTube: 10 Bí Mật Thú Vị Về Đại Dương

Upload caption:
10 Bí Mật Thú Vị Về Đại Dương - Part 1
10 Bí Mật Thú Vị Về Đại Dương - Part 2
```

Đường dẫn file như:

```text
processed\TikTok Kien Thuc\abc123\001.mp4
```

không ảnh hưởng đến caption.

---

## 12. Upload TikTok

Code chính:

```text
mmo_app\services.py
UploadHandler
```

Input:

```text
jobs.kind = upload
```

`UploadHandler.run(job)` join:

```text
upload_tasks
artifacts
tiktok_accounts
```

Lấy:

```text
artifact path
caption
account endpoint/CDP URL
```

### Publish guard

Nếu:

```text
settings.tiktok_publish_enabled = false
```

thì upload handler chuyển job sang manual action với thông báo bật quyền đăng TikTok.

Đây là guard an toàn vì upload có thể tạo nội dung công khai.

### Playwright/CDP

Upload dùng Chrome đã đăng nhập TikTok qua CDP URL:

```text
http://127.0.0.1:9222
```

Nếu TikTok yêu cầu đăng nhập/xác minh/captcha, handler phải chuyển `manual_action`, không đánh dấu completed.

Khi thành công:

```text
upload_tasks.status = completed
tiktok_accounts.posts_today += 1
media_items.status = uploaded
```

### Upload mode: CDP/Playwright hoặc GPM Automation

Mỗi TikTok account có trường:

```text
tiktok_accounts.mode
```

Giá trị hiện dùng:

```text
cdp
gpm
```

- `cdp`: dùng `endpoint` làm Chrome CDP URL và upload bằng Playwright.
- `gpm`: dùng `gpm_profile_id` để chuẩn bị tích hợp GPM Automation.

Ở bản hiện tại, `gpm` là option cấu hình/định tuyến trước. `UploadHandler` sẽ chuyển job sang `manual_action` với thông báo rõ rằng adapter GPM chưa được triển khai production. Khi tích hợp GPM thật, nên thêm adapter riêng thay vì nhét logic GPM trực tiếp vào `_playwright_upload()`.

---

## 13. Pipeline orchestrator

Code chính:

```text
mmo_app\services.py
PipelineOrchestrator
```

Orchestrator chạy một vòng worker:

```text
while running:
  recover expired leases
  claim next job in ("download", "process", "upload")
  dispatch handler
  complete/fail/manual_action
```

Handler mapping:

```text
download → DownloadHandler
process  → ProcessHandler
upload   → UploadHandler
```

Quy tắc retry:

- lỗi thông thường: `Database.fail(..., retry=True)`;
- quá số lần: `failed`;
- cần người dùng xử lý: `manual_action`;
- thành công: `completed`.

Khi thêm job kind mới, cần cập nhật:

1. schema nếu cần bảng mới;
2. handler mới;
3. orchestrator dispatch;
4. tests idempotency/retry;
5. UI dashboard nếu cần hiển thị riêng.

---

## 14. Desktop UI và lifecycle

Code chính:

```text
mmo_app\desktop.py
```

UI hiện có:

- toolbar Start/Pause;
- tab dashboard/jobs;
- tab `Luồng YouTube → TikTok`;
- tab `Cấu hình`;
- system tray.

Nguyên tắc:

- UI không xử lý tác vụ nặng trên Tk main thread.
- Widget gọi `Application`/service, không gọi worker trực tiếp.
- Đóng cửa sổ là ẩn xuống tray.
- Exit mới shutdown app.

Khi sửa UX, ưu tiên:

- báo lỗi ngay bằng messagebox nếu cấu hình sai;
- cập nhật status label sau Start/Pause;
- ghi job event/log để dev trace được.

---

## 15. Logging và debug

Logging được cấu hình trong:

```text
mmo_app\application.py
configure_logging(settings)
```

Dev nên debug theo thứ tự:

1. Dashboard job list.
2. `job_events`.
3. Log file trong app data.
4. SQLite database.
5. File artifact trong download/processed folder.

Các điểm cần log rõ:

- resolve YouTube URL thất bại;
- WebSub subscribe/verify/notification;
- RSS poll từng channel;
- yt-dlp download path;
- FFmpeg command/filter khi process lỗi;
- TikTok manual action/challenge.

---

## 16. Migration từ app cũ

Code hỗ trợ:

```text
mmo_app\migration.py
```

Nguyên tắc migration:

- không ghi ngược vào database cũ;
- import idempotent;
- không copy secret/profile Chrome ngầm;
- không để hai app cùng sở hữu một queue;
- giữ project cũ như nguồn tham khảo/module, không phát hành cho người dùng.

Hiện tại importer có hướng cho YouTube Tracker. Khi mở rộng importer cho video processor/TikTok, cần map dữ liệu vào schema chung:

```text
videos/channels cũ → channels/media_items/artifacts/jobs
processing projects cũ → artifacts/rendered hoặc bảng checkpoint tương ứng
TikTok config cũ → tiktok_accounts/channel_routes/upload_tasks
```

---

## 17. Test quan trọng

Test hiện tại:

```text
D:\mmo\mmo_app\tests\test_pipeline.py
```

Các nhóm cần giữ:

- discovery idempotent;
- WebSub notification là primary ingest;
- resolve YouTube URL;
- retry/recovery;
- process route đúng folder TikTok;
- tỷ lệ video mobile không kéo méo;
- split caption có `Part 1`, `Part 2`;
- overlay title/part dùng UTF-8 text files.

Lệnh test thường dùng:

```powershell
$env:PYTHONPATH='D:\mmo\mmo_app'
D:\mmo\youtube_tracker\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Nếu test gọi FFmpeg thật, cần đảm bảo FFmpeg path đúng hoặc dùng binary bundled.

---

## 18. Build Windows

Script:

```text
D:\mmo\mmo_app\build_windows.ps1
```

Chạy trong:

```powershell
cd D:\mmo\mmo_app
.\build_windows.ps1
```

Output mong muốn:

```text
D:\mmo\mmo_app\dist\MMOApp\MMOApp.exe
D:\mmo\mmo_app\dist\MMOApp-Windows-x64.zip
```

Không build sang `dist_websub` hoặc `dist_release` cho release hiện tại, trừ khi chỉ dùng làm thử nghiệm riêng.

Nếu build lỗi `PermissionError` trong `dist\MMOApp\_internal`, thường là app đang chạy ở tray. Dừng process `MMOApp.exe` rồi build lại.

---

## 19. Checklist khi sửa code

Trước khi sửa:

- đọc `INTEGRATION_SPEC.md`;
- đọc `MIGRATION_PLAN.md`;
- đọc file service/UI/schema liên quan;
- xác định thay đổi thuộc stage nào: YouTube, process, upload, UI, DB, build.

Khi sửa schema:

- tăng `SCHEMA_VERSION`;
- cập nhật `schema.sql`;
- thêm migration an toàn;
- test database cũ mở lại được.

Khi sửa pipeline:

- đảm bảo idempotency;
- không tạo job trùng;
- restart giữa stage vẫn resume được;
- lỗi transient phải retry;
- lỗi cần người dùng phải `manual_action`.

Khi sửa TikTok upload:

- không tự publish nếu `tiktok_publish_enabled=false`;
- captcha/login/verification không được đánh dấu success;
- caption phải lấy từ `upload_tasks.caption`.

Khi sửa process video:

- không lấy caption từ tên file;
- output phải nằm trong folder TikTok route;
- FFmpeg filter không được kéo méo video;
- overlay tiếng Việt phải an toàn với Unicode/path Windows.

Khi sửa UI:

- không block Tk main thread;
- cấu hình sai phải báo rõ;
- Start/Pause phải có phản hồi trạng thái;
- layout nhiều YouTube → một TikTok phải dễ hiểu.

---

## 20. Ví dụ end-to-end cho dev trace

Giả sử người dùng cấu hình:

```text
Layout: Kien Thuc
TikTok account: TikTok Kien Thuc
CDP URL: http://127.0.0.1:9222
YouTube URL: https://www.youtube.com/@tien5140
```

Khi lưu route:

```text
resolve @tien5140 → UC8YTaII3UJzSDRpt3JS7LpQ

channels:
  channel_id = UC8YTaII3UJzSDRpt3JS7LpQ

tiktok_accounts:
  name = TikTok Kien Thuc
  endpoint = http://127.0.0.1:9222

channel_routes:
  name = Kien Thuc
  account_id = TikTok Kien Thuc

channel_route_sources:
  route_id = Kien Thuc
  channel_id = UC8YTaII3UJzSDRpt3JS7LpQ
```

Khi YouTube báo video mới:

```text
media_items:
  youtube_video_id = abc123
  channel_id = UC8YTaII3UJzSDRpt3JS7LpQ
  title = "Một video ví dụ"

jobs:
  kind = download
  idempotency_key = download:abc123
```

Sau download:

```text
artifacts:
  kind = download
  path = downloads\...\abc123.mp4

jobs:
  kind = process
```

Sau process, nếu chia 2 part:

```text
artifacts:
  kind = rendered
  segment_index = 1
  path = processed\TikTok Kien Thuc\abc123\001.mp4

artifacts:
  kind = rendered
  segment_index = 2
  path = processed\TikTok Kien Thuc\abc123\002.mp4
```

Upload tasks:

```text
upload_tasks:
  caption = "Một video ví dụ - Part 1"

upload_tasks:
  caption = "Một video ví dụ - Part 2"
```

Upload jobs:

```text
jobs:
  kind = upload
  media_item_id = <id abc123>
```

Khi upload thành công:

```text
upload_tasks.status = completed
media_items.status = uploaded
jobs.status = completed
```

---

## 21. Ranh giới chưa nên phá

Không nên:

- chạy lại UI/tray riêng của project cũ;
- tạo thêm EXE phụ cho người dùng;
- dùng database riêng cho từng module;
- để downloader/processor/uploader tự quản lifecycle riêng;
- upload công khai khi chưa bật publish guard;
- lấy caption từ tên file;
- để một kênh YouTube route sang nhiều TikTok layout mà không có rule rõ ràng;
- build release vào thư mục khác `dist`.
