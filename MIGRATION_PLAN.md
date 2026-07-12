# MMO App — kế hoạch migration

## Nguyên tắc

- Không sửa hoặc xóa ba project nguồn trong quá trình migration.
- Tách core khỏi UI/lifecycle trước, rồi mới hợp nhất dữ liệu và màn hình.
- Mỗi bước có compatibility adapter và test; không chạy hai worker cùng sở hữu một queue.

## Các pha

### Pha 0 — baseline

Chạy test hiện có, ghi nhận dependencies/binary và snapshot schema. Xác định dữ liệu cũ chỉ được import theo yêu cầu; mặc định MMO App tạo data root mới để tránh hai app cùng ghi.

### Pha 1 — nền tảng thống nhất

Tạo composition root, settings JSON, logging, SQLite migration, repository và orchestrator. Thêm fake handlers để kiểm thử toàn bộ state machine không cần mạng, FFmpeg hay TikTok.

### Pha 2 — adapter YouTube

Port WebSub/RSS/downloader thành service không UI. Thay global config/session bằng dependency injection. Khi download commit, ghi artifact và enqueue process trong cùng transaction. Giữ RSS reconciliation.

### Pha 3 — adapter processor

Port scanner/project service và phase executors. Nhận artifact trực tiếp trước, scanner chỉ bù lỗi. Map checkpoint cũ vào database chung và phát rendered artifacts sau finalize.

### Pha 4 — TikTok production queue

Port browser/GPM/uploader; thêm accounts, upload tasks, scheduler, per-account lock, concurrency, daily limit, retry và manual-action. Không tự publish khi `publish_enabled=false`.

### Pha 5 — UI/tray/lifecycle

Một Tk root, một tray, một controller. Tab hóa dashboard và cấu hình. Log realtime qua queue thread-safe. Shutdown theo thứ tự và single-instance lock.

### Pha 6 — chuyển dữ liệu

Cung cấp importer idempotent cho `youtube_tracker.db` và `video_processor.db`: channels/videos trước, processing project/artifacts sau. Không copy secret/profile Chrome ngầm; người dùng chọn profile TikTok và xác nhận đường dẫn.

### Pha 7 — build và hardening

Gộp requirements, spec PyInstaller và binary. Chạy unit/integration/smoke executable; kiểm tra Unicode path, restart giữa stage, lease recovery và không có child EXE sản phẩm cũ.

## Rollback

Ba source project giữ nguyên. Nếu migration lỗi, dừng MMO App và dùng database backup tạo trước import. Không có migration nào ghi ngược vào database cũ.

