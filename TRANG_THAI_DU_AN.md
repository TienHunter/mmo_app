# Trạng thái dự án — bàn giao cho phiên chat mới

Cập nhật: 2026-11-12

Đọc file này trước khi sửa code. Nó ghi trạng thái thực tế, các quyết định đã
chốt, chỗ đang dở và cách kiểm tra. Tài liệu liên quan:

- `SPEC_VIDEO_PROCESSING_RULES.md` — spec xử lý video/caption.
- `NOTES_CAPTION_COMPOSE.md` — caption + AI.
- `NOTES_SELECTOR_LOCALIZATION.md` — selector TikTok còn bám text.

## 1. Project là gì

MMO App: desktop Windows một tiến trình, tự động hóa:

```text
Theo dõi kênh YouTube → tải video mới → xử lý/cắt video → đăng TikTok
```

UI PySide6 mới trong `mmo_app/ui/`; core nằm ở `Application`, `services.py`,
`database.py`, `settings.py`. UI Tkinter cũ `mmo_app/desktop.py` không còn là
entry point.

## 2. Môi trường, chạy, test

- Python 3.12, PySide6-Essentials, SQLite WAL.
- Entry point: `main.py` → `mmo_app/ui/main_window.py`.
- Chỉ dùng `.venv` trong `D:\mmo\mmo_app`.

```powershell
cd D:\mmo\mmo_app
.\.venv\Scripts\python.exe main.py
```

```powershell
cd D:\mmo\mmo_app
.\.venv\Scripts\python.exe -m compileall -q mmo_app
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Trạng thái test lúc bàn giao: **79 tests OK** (2026-11-12).

Dữ liệu runtime trên máy này:

- DB: `%LOCALAPPDATA%\MMOApp\mmo_app.db`
- Settings: `%LOCALAPPDATA%\MMOApp\settings.json`
- Log file: `%LOCALAPPDATA%\MMOApp\logs\mmo_app.log`

Snapshot cấu hình đang dùng (có thể đã đổi sau này):

- `video_speed = 1.3`, `video_zoom_percent = 120` (giá trị fallback global).
- Tài khoản TikTok 1: active, speed `1.2`, zoom `100×140`,
  output `D:/video_mmo/mexico_test`.
- Kênh YouTube đang route: `Todo Pintado` (`UC4qF34Di2QIQ_oouiF88_4g`).

## 3. Luồng hoạt động

```text
WebSub/RSS phát hiện video
  → media_items + job download
  → DownloadHandler: tải file, kêu chuông, enqueue process
  → ProcessHandler: cắt/render, lưu artifacts, tạo upload_tasks
  → UploadHandler: mở GPM profile, Playwright đăng TikTok
```

Job nằm trong bảng `jobs`, trạng thái `queued/leased/retry_wait/completed/
failed/manual_action/cancelled`. `PipelineOrchestrator` claim job theo loại.

## 4. Schema / migration

`SCHEMA_VERSION = 8`, migration tự chạy trong `Database.migrate()`.

Các cột quan trọng:

- `tiktok_accounts`:
  - cấu hình xử lý: `speed`, `zoom_width_percent`, `zoom_height_percent`,
    `split_enabled`, `overlay_enabled`, `background_path`, `output_dir`,
    `notification_sound`
  - caption: `caption_mode` (`fixed`/`ai`), `caption_text`, `hashtags`
  - đăng bài: `publish_enabled`, `daily_limit`, `posts_today`, `counter_date`,
    `active`, `gpm_profile_id`
  - AI riêng: `ai_api_url`, `ai_api_key`, `ai_model`, `ai_system_prompt`
- `upload_tasks`: `is_test`, `status` (có `test_ready`), `caption`,
  `scheduled_at`, `posted_at`.
- `channel_route_sources`, `channel_routes`: route kênh YouTube → tài khoản.

Cột nullable có ý nghĩa kế thừa (NULL = lấy global): `split_enabled`,
`overlay_enabled`, `publish_enabled`, các cột AI. `output_dir = ''` dùng thư
mục chung.

## 5. Quy tắc xử lý video hiện tại

- Đầu ra cố định 9:16, canvas 1080×1920, nền đen, `scale + pad`, `libx264`,
  `-crf 18 -pix_fmt yuv420p`.
- Speed áp dụng trước, chia phần theo thời lượng **sau speed**:
  - `<5 phút`: 1 phần
  - `5–8`: 2, `8–15`: 3, `15–18`: 4, `≥18`: 5
- Tối đa 5 phần. Các phần không bằng hệt nhau: offset `+5%, -3%, +4%, -2%`,
  phần cuối hấp thụ dư, luôn khoảng 90–110% độ dài trung bình.
- Zoom rộng/cao độc lập. Nếu hai hệ số khác nhau: scale vừa canvas rồi scale
  riêng từng chiều (chiều cao 140% cao lên thật, có thể giãn không đều). Nếu
  bằng nhau: dùng fit/crop/pad như cũ.
- Tên file: `{title}_{N}.mp4` trong `output_dir` của tài khoản (1 phần vẫn `_1`).
- Caption overlay (bật bằng `overlay_enabled` của tài khoản):
  - Font **Arial Bold** (`arialbd.ttf`), fallback Arial.
  - Nền trắng bo góc 18px, ôm chữ, padding 10px, chữ đen.
  - Tự xuống dòng, tối đa 3 dòng, rộng tối đa 90% canvas, từ dài bị ngắt ký tự.
  - Đặt **phía trên video trong vùng đen**, chừa safe top 16% canvas để không
    đè thanh "For You" của TikTok; nếu video phủ kín canvas thì bám mép trên.
- Caption text trên video dùng `{title} ({part})`; 1 phần chỉ `{title}`.

## 6. Cấu hình theo tài khoản + ý nghĩa active

Toàn bộ cấu hình xử lý/đăng nằm trong tài khoản TikTok. Dialog tài khoản điền
sẵn default từ `Settings`; global chỉ còn là default/fallback.

- `active = 1`: dùng account cho route, cắt video và đăng.
- `active = 0`:
  - **Vẫn** tải video, kêu chuông, cắt/render theo đúng cấu hình tài khoản
    (speed, zoom, split, overlay, background, caption, AI, output).
  - **Không** tạo `upload_tasks` mới, không mở GPM profile, không đăng.
  - Job upload đã có trong queue sẽ báo `ManualAction`: tài khoản đang tắt.
  - Test video bỏ qua `active`, vẫn dùng tài khoản theo ID.
  - Video đã xử lý trong lúc tắt **không tự vào queue lại** khi bật lại.

Caption khi đăng:

- `caption_mode = fixed`: dùng `caption_text`; nếu trống thì dùng title YouTube
  (+ `({part})` nếu nhiều phần) rồi hashtag của tài khoản ở dòng dưới.
- `caption_mode = ai`: gọi OpenAI-compatible bằng AI config riêng của tài khoản,
  field trống fallback global; lỗi thì fallback caption fixed/title/hashtag.
- Không còn dùng `caption_include_title`, `caption_include_hashtags`,
  `hashtag_source` cho flow tài khoản; các field này chỉ còn trong Settings cũ.

## 7. Pipeline song song

`PipelineOrchestrator` spawn 3 nhóm worker thread, mỗi nhóm claim riêng loại job:

- `max_download_workers` — mặc định **2**
- `max_process_workers` — mặc định **1**
- `max_upload_workers` — mặc định **1**

Chỉnh ở **Cài đặt → Hệ thống** (1–10). Worker count chỉ đọc lúc `start()`;
đổi xong phải Dừng → Bắt đầu. Các video khác nhau có thể overlap tải/xử lý/đăng;
thứ tự từng video vẫn download → process → upload.

Upload cùng một tài khoản bị khóa bằng `threading.Lock` cấp class
(`UploadHandler._account_lock`), tránh mở trùng GPM profile. Manual upload dùng
chung khóa này.

## 8. Test video

- Nút **Test video** trên card tài khoản → nhập link YouTube.
- `YouTubeService.video_id_from_url` nhận `watch?v=`, `youtu.be`, `shorts`, `live`.
- `queue_test_video`: tạo/tái dùng `media_items`, enqueue download với payload
  `{"test_account_id": id, "is_test": 1}`.
- Download/process mang payload; process dùng account override kể cả inactive.
- Output vào `output_dir` của tài khoản; upload task `is_test=1`,
  status `test_ready`, **không tạo upload job**, không tự đăng.
- Queue có cột **Test**. UploadHandler có guard không bao giờ đăng task test.

## 9. Chuông, thời gian, UI

- Chuông kêu **ngay khi bắt đầu download**, dùng `notification_sound` của
  tài khoản route/test. Hỗ trợ WAV/MP3 (MP3 convert tạm qua ffmpeg).
- Chuông lặp tối đa 30 giây, nút **Tắt chuông** ở status bar khi đang kêu.
- Mọi thời gian UI hiển thị theo **UTC+7** (`format_local_time`,
  `local_today`). KPI hôm nay và biểu đồ 7 ngày cũng theo UTC+7.
- Panel phải **Trạng thái hoạt động**:
  - Refresh 1 giây; 3 tầng Tải/Xử lý/Đăng.
  - Đang chạy: progress + danh sách **mỗi task 1 dòng** (video, kênh, tài khoản,
    ETA riêng); ETA dựa median 10 job hoàn tất gần nhất cùng loại.
  - Chờ/vàng, hoàn tất ✓ xanh, lỗi ✕ đỏ, cần can thiệp !
  - 20 hoạt động gần nhất dạng card: giờ xanh `#2563EB`, nội dung weight 600.
  - Nút topbar **Hoạt động** ẩn/hiện, ghi nhớ trạng thái.
- Bảng:
  - `CopyableTable` cho video/kênh/queue/logs; chọn ô kéo chuột, Ctrl+C,
    chuột phải tự chọn ô rồi **Sao chép**.
  - Kéo cột, line lưới, màu xen kẽ (light `#FFFFFF/#F1F4F8`, dark
    `#182235/#222E42`), hover cả dòng (light `#E6EEFF`, dark `#2B3E5C`).
  - Phân trang client-side mặc định 20 dòng/trang, chọn 20/50/100.
- Chuột không đổi giá trị `QSpinBox/QDoubleSpinBox/QComboBox` (wheel guard
  trong `main_window.run`).
- Kênh theo dõi: nút Sửa/Tạm dừng/Xóa nằm ở cột cuối **cùng bảng**; Làm mới
  cạnh Thêm kênh. Thêm/bật lại kênh tự gọi refresh WebSub subscription ngay.
- Log job có chi tiết:
  - `Bắt đầu tải video: {title} | Kênh: {channel}`
  - `Bắt đầu xử lý video: ...`
  - `Bắt đầu đăng TikTok: ... | Kênh: ... | Tài khoản: ...`
  - `Hoàn tất tải video/xử lý video/đăng TikTok: ...`

## 10. WebSub / RSS / Cloudflare

- WebSub server mặc định `127.0.0.1:8000`, callback `/webhook`.
- Cloudflare Tunnel trỏ `http://127.0.0.1:8000`; domain public trong Cài đặt.
- WebSub push chỉ ảnh hưởng độ trễ phát hiện video, không ảnh hưởng tốc độ tải.
- Kênh mới: route dùng ngay; RSS quét ở chu kỳ kế tiếp (`rss_interval_seconds`,
  mặc định 1800s); WebSub subscription được refresh ngay khi app đang chạy,
  YouTube xác minh async. Nếu lỗi/không chắc: Dừng → Bắt đầu để force renew.
- Xóa/tạm dừng kênh chưa unsubscribe WebSub; subscription cũ có thể còn đến hết hạn.

## 11. File quan trọng

```text
mmo_app/
  services.py              # download/process/upload/GPM/WebSub pipeline
  application.py           # vòng đời app, WebSub refresh
  database.py, schema.sql  # DB + migration
  settings.py              # Settings + defaults
  websub.py                # HTTP callback, subscribe/renew
  ui/
    main_window.py         # shell, panel, tray, wheel guard
    components/            # ActivityPanel, CopyableTable, DataTable, TablePager, ...
    pages/                 # dashboard/accounts/videos/channels/queue/logs/settings
    dialogs/               # AccountDialog, ChannelDialog
    services/repository.py # SQL cho UI, format UTC+7, pipeline_activity/ETA
    theme/tokens.py        # QSS light/dark
tests/test_pipeline.py     # 79 tests
```

`mmo_app/ui/` là code mới, **chưa commit** trong git tại thời điểm bàn giao.
Working tree có nhiều file sửa chưa commit; nhánh `main`. `dist_preview/` chỉ là
ảnh render thử, có thể bỏ qua/xóa.

## 12. Việc còn dở / rủi ro

1. **Selector TikTok** đang bám text; cần chuyển `data-e2e`/`data-testid`/class TUX
   trước, text làm fallback. Xem `NOTES_SELECTOR_LOCALIZATION.md`.
2. `mmo_app/desktop.py` là UI Tkinter cũ, còn field CDP; không dùng. Cân nhắc xóa.
3. `HUONG_DAN_SU_DUNG.md` / `FLOW_UPLOAD_TIKTOK.md` còn mô tả CDP, chưa cập nhật GPM.
4. `placeholder_page.py` không dùng; xóa khi tiện.
5. Đổi cấu hình xử lý/caption không reprocess video đã xử lý; muốn áp dụng phải
   chạy lại/test lại.
6. Tài khoản tắt: video đã cắt không tự tạo upload task khi bật lại.
7. ETA chỉ là median lịch sử, không chính xác theo dung lượng video.
8. Phân trang là client-side; repository vẫn fetch tối đa 500 dòng (logs export
   10000), chưa lazy-load server-side.
9. Cột thao tác kênh nằm cuối bảng, có thể phải cuộn ngang khi cửa sổ hẹp;
   không phải frozen column.
10. Xóa kênh không unsubscribe WebSub.
11. `caption_include_title`, `caption_include_hashtags`, `hashtag_source` là cấu
    hình cũ, không còn ảnh hưởng flow tài khoản.
12. Upload song song phụ thuộc GPM API; nếu lỗi khi tăng worker, hạ
    `max_upload_workers = 1`.

## 13. Quyết định không nên phá

- UI chỉ là tầng hiển thị; SQL gom ở `UIRepository`, logic nghiệp vụ ở core.
- Mọi timestamp DB là UTC; UI đổi UTC+7, không lưu giờ local.
- Job idempotency key phải giữ ổn định; test job dùng key riêng để chạy lặp.
- Upload luôn qua GPM profile; không quay lại CDP.
- Task test `is_test` tuyệt đối không được đăng.
- Tài khoản inactive vẫn dùng config để cắt; chỉ chặn GPM/đăng.
- File video đặt tên `{title}_{N}.mp4`; caption `{title} ({N})` khi nhiều phần.
- Không thêm hook nặng vào pipeline chỉ để phục vụ UI; panel đọc DB định kỳ.

## 14. Checklist phiên mới

1. Đọc file này + 3 file `NOTES_*`/`SPEC_*`.
2. `git status` để biết phần chưa commit.
3. Chạy `unittest discover -s tests -v`; kỳ vọng 79 tests OK trở lên.
4. Nếu sửa pipeline, kiểm tra cả job download/process/upload và test video.
5. Nếu sửa UI bảng, giữ chọn/copy, kéo cột, xen kẽ, phân trang, line lưới.
6. Nếu sửa caption, nhớ `{title} ({part})`, không dùng chữ "Phần" trong caption;
   tên file vẫn `{title}_{N}.mp4`.
7. Nếu sửa account active, giữ quy tắc: inactive vẫn cắt theo config, chỉ chặn
   mở GPM/đăng.
8. Chạy app thật để kiểm tra UI:
   `.\.venv\Scripts\python.exe main.py`
