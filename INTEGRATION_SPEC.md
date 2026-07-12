# MMO App — đặc tả tích hợp

## 1. Mục tiêu và ranh giới

MMO App là tiến trình desktop Windows duy nhất quản lý chuỗi công việc:

`YouTube discovery -> download -> process -> TikTok upload`

Người dùng chỉ mở `MMOApp.exe`. Các năng lực cũ được giữ dưới dạng module nội bộ, không khởi chạy UI, tray, database hay lifecycle riêng. Bản phát hành là một thư mục tự chứa với đúng một entry point công khai.

## 2. Kết quả khảo sát

### YouTube Tracker

- Python/Tkinter, Flask + Waitress, APScheduler, SQLAlchemy/SQLite, yt-dlp.
- WebSub và RSS cùng ghi vào bảng `videos`; downloader claim theo trạng thái và có backoff.
- Desktop hiện tự sở hữu form cấu hình, web server, scheduler, downloader, tray và shutdown.
- Artifact bàn giao đáng tin cậy nhất là `videos.local_path` khi trạng thái `downloaded`.
- Bản build hiện bundle Node và FFmpeg, lưu dữ liệu tại `%LOCALAPPDATA%\YouTubeTracker`.

### Video Processor

- Python/Tkinter, sqlite3, worker bền vững theo 9 checkpoint.
- Scanner nhận file ổn định, FFprobe, chống trùng; phase 2 transcript/dịch/phụ đề; phase 3 cắt/render/finalize.
- Dữ liệu có thể resume trong `processing_projects`, `pipeline_steps`, `transcript_cues`, `video_segments`.
- Desktop hiện tự sở hữu config, worker, tray và shutdown.
- Bản build bundle FFmpeg/FFprobe; model Whisper nằm ngoài gói và tải ở lần dùng đầu.

### TikTok Auto Uploader

- Mới có uploader đồng bộ Playwright, kết nối Chrome persistent/CDP/GPM và phát hiện challenge cơ bản.
- Chưa có UI, database, scheduler hay worker pool thực tế; `db` và `ui` mới là placeholder.
- Upload cần browser profile đã đăng nhập. Chế độ publish phải là lựa chọn rõ ràng; dry-run không được ghi nhận là đã đăng.

## 3. Kiến trúc đích

Một process sở hữu duy nhất:

- `AppController`: start/stop, recovery, single-instance và shutdown có thứ tự.
- `Settings`: một file `%LOCALAPPDATA%\MMOApp\settings.json`, đường dẫn tuyệt đối, ghi atomic.
- `Database`: một SQLite `%LOCALAPPDATA%\MMOApp\mmo_app.db`, WAL + foreign key + migration có version.
- `PipelineOrchestrator`: claim job theo lease, retry/backoff, chuyển stage idempotent.
- `YouTubeService`: WebSub/RSS/download; phát sự kiện nội bộ sau khi file tải hoàn tất.
- `ProcessingService`: ingest trực tiếp artifact download và giữ scanner reconciliation làm fallback.
- `TikTokService`: profile/account, lịch đăng, upload worker có giới hạn concurrency và manual-intervention state.
- `DesktopUI`: một cửa sổ, các tab Tổng quan / YouTube / Xử lý / TikTok / Cấu hình / Log.
- `TrayController`: đúng một icon; Show, Start/Pause pipeline, Open data, Exit.
- `Logging`: một rotating file và event stream cho UI; mọi record mang `pipeline_item_id`, `stage`, `account_id` nếu có.

Các module không import config/database singleton của nhau. Dependency được truyền từ composition root. Tác vụ nặng không chạy trên Tk main thread.

## 4. Mô hình dữ liệu thống nhất

- `channels`: cấu hình theo dõi và lease WebSub.
- `media_items`: danh tính video YouTube duy nhất, metadata nguồn và trạng thái tổng.
- `artifacts`: file download, audio, subtitle, segment và rendered output; unique theo `(media_item_id, kind, path)`.
- `jobs`: hàng đợi chung; `kind` là `download`, `process`, `upload`; có status, attempts, `available_at`, lease và lỗi.
- `job_events`: lịch sử append-only phục vụ audit/log UI.
- `processing_projects`, `pipeline_steps`, `transcript_cues`, `video_segments`: giữ checkpoint chi tiết của processor, liên kết `media_item_id`.
- `tiktok_accounts`: account/GPM/CDP/profile, giới hạn ngày, trạng thái pause/manual-action.
- `upload_tasks`: liên kết rendered artifact với account, caption, schedule, idempotency key và kết quả.
- `settings`: metadata schema và các cờ runtime không chứa secret dạng loggable.

Trạng thái job chuẩn: `queued`, `leased`, `retry_wait`, `paused`, `manual_action`, `completed`, `failed`, `cancelled`. Restart đưa lease quá hạn về `queued`; unique key ngăn download/process/upload trùng.

## 5. Contract giữa các stage

1. Download hoàn tất chỉ khi file tồn tại, khác 0 byte và `local_path` đã commit.
2. Orchestrator tạo đúng một process job bằng idempotency key `process:<media_item_id>:<download_artifact_id>`.
3. Process hoàn tất khi có ít nhất một rendered artifact hợp lệ. Mỗi segment là một upload candidate độc lập.
4. Routing gán candidate vào account đã active; nếu chưa có account thì giữ `waiting_route`, không làm thất bại pipeline.
5. Upload claim phải tuân thủ schedule, daily limit và account lock. Thành công chỉ sau tín hiệu success của TikTok; captcha/verification chuyển `manual_action`.

## 6. Giao diện và vận hành

- Dashboard hiển thị số lượng theo stage/trạng thái và item gần nhất.
- Mọi hành động Start/Pause/Retry/Cancel đi qua controller, không gọi worker trực tiếp từ widget.
- Cấu hình lần đầu cho phép RSS-only để chạy không cần public domain; TikTok có thể chưa cấu hình và pipeline dừng an toàn ở đầu ra processed.
- Đóng cửa sổ là ẩn xuống tray; Exit mới dừng nhận job, chờ worker trong timeout, giải phóng browser/server/scheduler rồi đóng DB.
- Không tự động xóa video nguồn hoặc artifact.

## 7. Đóng gói và tiêu chí nghiệm thu

- PyInstaller one-directory, một `MMOApp.exe`; bundle FFmpeg/FFprobe, Node, Playwright driver/browser cần thiết và font.
- Không phân phối `YouTubeTracker.exe` hoặc `VideoProcessor.exe`.
- Dữ liệu mutable chỉ nằm dưới `%LOCALAPPDATA%\MMOApp`; media ở thư mục người dùng cấu hình.
- Smoke test trên Windows: start/exit/tray, DB migration/recovery, enqueue download->process->upload (provider/browser fake), và launch executable.
- Upload thật là kiểm thử có chủ đích vì cần phiên TikTok đăng nhập và có thể tạo nội dung công khai.

