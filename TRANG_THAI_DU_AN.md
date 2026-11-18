# Trạng thái dự án — bàn giao cho phiên chat mới

Cập nhật: 2026-11-15

Đọc file này trước khi sửa code. Nó ghi trạng thái thực tế, các quyết định đã
chốt, chỗ đang dở và cách kiểm tra. Tài liệu liên quan:

- `CLAUDE.md` — hướng dẫn ngắn tự nạp mỗi phiên; trỏ về file này.
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

Trạng thái test lúc bàn giao: **92 tests OK** (2026-11-15).

Dữ liệu runtime trên máy này:

- DB: `%LOCALAPPDATA%\MMOApp\mmo_app.db`
- Settings: `%LOCALAPPDATA%\MMOApp\settings.json`
- Log file: `%LOCALAPPDATA%\MMOApp\logs\mmo_app.log`

Snapshot cấu hình đang dùng (có thể đã đổi sau này):

- `video_speed = 1.3`, `video_zoom_percent = 120` (giá trị fallback global).
- `video_encoder = cuda`; `download_quality = best`.
- AI: `ai_api_url = https://api.deepseek.com/chat/completions`,
  `ai_model = deepseek-chat`; API key chưa điền.
- Tài khoản TikTok `tiktok_test_1` / `tiktok_test_2`: speed `1.15`,
  zoom `100×140`, overlay bật, output `D:/video_mmo/mexico_test_*`.
- Kênh YouTube đang route: `Todo Pintado` (`UC4qF34Di2QIQ_oouiF88_4g`).

## 3. Luồng hoạt động

```text
WebSub phát hiện video
  → media_items + job download
  → DownloadHandler: tải file, kêu chuông, enqueue process
  → ProcessHandler: cắt/render, lưu artifacts, tạo upload_tasks
  → UploadHandler: mở GPM profile, Playwright đăng TikTok
```

Job nằm trong bảng `jobs`, trạng thái `queued/leased/retry_wait/completed/
failed/manual_action/cancelled`. `PipelineOrchestrator` claim job theo loại.

- **Kill job** (nút ở Tổng quan, có popup xác nhận): job đang `leased` →
  `manual_action` với message "Đã dừng bởi Kill job; bấm Thử lại để chạy lại"
  nên hiện ở **Cần xử lý**; ghi `job_events` WARNING cho từng job và một dòng
  hệ thống `Kill job: đã dừng X job, dừng Y tiến trình đang chạy` (hiện ở Nhật
  ký/Hoạt động); sau đó kill tiến trình con đang chạy (`run_tracked`
  bọc Popen; hiện áp cho 3 lệnh ffmpeg encode của ProcessHandler). Worker nhận
  `JobKilled`, không ghi đè trạng thái; `complete()/fail()/manual_action()` chỉ
  đổi job đang `leased`. Giới hạn: download yt-dlp và upload Playwright chạy
  trong tiến trình app nên chỉ được đánh dấu dừng, không ngắt ngay.

## 4. Schema / migration

`SCHEMA_VERSION = 8`, migration tự chạy trong `Database.migrate()`.

Các cột quan trọng:

- `tiktok_accounts`:
  - cấu hình xử lý: `speed`, `zoom_width_percent`, `zoom_height_percent`,
    `split_enabled`, `overlay_enabled`, `background_path`, `output_dir`,
    `notification_sound`
  - caption: `caption_mode` (`''` = theo global, `fixed`, `ai`), `caption_text`
    (legacy), `hashtags`
  - đăng bài: `publish_enabled`, `daily_limit`, `posts_today`, `counter_date`,
    `active`, `gpm_profile_id`
  - AI riêng: `ai_api_url`, `ai_api_key`, `ai_model`, `ai_system_prompt` (legacy,
    fallback khi Settings trống)
- `media_items`: `caption` lưu caption gốc sinh ra ở bước process (AI/fixed,
  kể cả tài khoản đang tắt nên không có `upload_tasks`).
- `upload_tasks`: `is_test`, `status` (có `test_ready`), `caption`,
  `scheduled_at`, `posted_at`, `created_at` (UTC; row cũ NULL thì UI fallback
  `media_items.updated_at`).
- Trang **Hàng đợi & lịch đăng** hiển thị cả video đã render nhưng chưa có
  `upload_tasks` (tài khoản route đang tắt) với trạng thái **Chưa xếp lịch**,
  nên video nhận từ WebSub vẫn thấy trong queue; cột **Tạo lúc** theo UTC+7,
  cột **Đường dẫn** là file render. Cột **Caption** đã chuyển sang trang
  **Thư viện video** (đọc `media_items.caption`).
- Đăng thủ công: chọn dòng trong bảng Hàng đợi rồi bấm
  **Đánh dấu đã đăng** → `upload_tasks.status='completed'`, điền `posted_at`,
  `media_items.status='uploaded'`. Dòng **Chưa xếp lịch** được tạo
  `upload_tasks` completed với account của route; dòng không có account hoặc
  task test bị bỏ qua. Job upload đã xếp lịch mà task đã completed sẽ tự bỏ
  qua, không đăng lại.
- **Thu hồi đã đăng**: chọn dòng đang Đã đăng rồi bấm nút → task có job upload
  trở về `pending` và xóa `posted_at`; task do Đánh dấu tạo ra (không có job
  `upload:{id}`) bị xóa để dòng trở lại **Chưa xếp lịch**. `media_items` về
  `processed` nếu không còn task completed nào khác của video đó.
- `channel_route_sources`, `channel_routes`: route kênh YouTube → tài khoản.

Cột nullable có ý nghĩa kế thừa (NULL = lấy global): `split_enabled`,
`overlay_enabled`, `publish_enabled`. `output_dir = ''` dùng thư mục chung.
`caption_text` và `ai_api_*` của tài khoản là legacy; caption/AI giờ cấu hình
chung ở `Settings`, cột tài khoản chỉ còn làm fallback.

## 5. Quy tắc xử lý video hiện tại

- Đầu ra cố định 9:16, canvas 1080×1920, nền đen, `scale + pad`. Mặc định CPU
  `libx264 -crf 18 -pix_fmt yuv420p`.
- Bộ mã hóa chọn ở **Cài đặt → Hệ thống**: `auto` (dò CUDA/QSV/AMF rồi
  fallback CPU), `cuda` (CUDA CQ23), `cuda_cq27`, `cuda_cq27_max`
  (CQ27 + maxrate 2.5M/bufsize 5M), `cpu` (libx264), `intel` (h264_qsv),
  `amd` (h264_amf). Các mức `cuda*` dùng full pipeline GPU; lỗi thì tự chạy
  lại bằng CPU. Giá trị cũ `nvidia` được map sang `cuda`.
  Zoom >100 hoặc zoom rộng/cao lệch dùng GPU scale rồi hwdownload để crop/pad
  CPU, sau đó hwupload lại. Caption CUDA là nền trắng không bo góc (NV12);
  CPU giữ bo góc.
- Speed áp dụng trước, chia phần theo thời lượng **sau speed**. Bốn mốc chỉnh
  ở **Cài đặt → Cắt video tự động** (`split_threshold_seconds`, mặc định
  300/480/900/1080 giây; nút **Mặc định** trả 4 mốc về giá trị này):
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

Cấu hình xử lý/đăng nằm trong tài khoản TikTok. Dialog tài khoản điền sẵn
default từ `Settings`; global còn là default/fallback. Caption cố định + AI
cấu hình chung ở Settings; tài khoản chỉ chọn nguồn caption và hashtag.

- `active = 1`: dùng account cho route, cắt video và đăng.
- Card tài khoản có nút **Nhân bản**: copy toàn bộ cấu hình sang tài khoản mới,
  tạo ở trạng thái tạm dừng để nhập profile mới rồi bật. Dialog Thêm/Nhân bản tự
  sinh **GPM Profile ID** dạng `uuid4` khi ô trống; sửa tài khoản giữ ID đã lưu.
- `active = 0`:
  - **Vẫn** tải video, kêu chuông, cắt/render theo đúng cấu hình tài khoản
    (speed, zoom, split, overlay, background, caption, AI, output).
  - **Không** tạo `upload_tasks` mới, không mở GPM profile, không đăng.
  - Job upload đã có trong queue sẽ báo `ManualAction`: tài khoản đang tắt.
  - Test video bỏ qua `active`, vẫn dùng tài khoản theo ID.
  - Video đã xử lý trong lúc tắt **không tự vào queue lại** khi bật lại.

Caption khi đăng:

- Cấu hình caption cố định + AI (URL/key/model/system prompt) nằm ở
  **Cài đặt → Caption mặc định**. Tài khoản TikTok chọn **Nguồn caption** trong
  dialog Sửa tài khoản, nhóm **Tài khoản**: `Theo Cài đặt chung` (để trống),
  `Caption cố định`, hoặc `Sinh caption bằng AI`.
- Caption/hashtag được sinh **một lần khi bắt đầu process** (title đã có), trước
  khi render các phần; không gọi AI lặp lại cho từng phần. AI thành công ghi event
  `AI đã sinh caption/hashtag cho video: ...`.
- `caption_mode` global là mặc định; mode của tài khoản ghi đè nếu có.
  `caption_text` global được ưu tiên, fallback cột `caption_text` cũ của tài khoản.
- Caption fixed dùng `caption_text`; nếu trống thì dùng title YouTube
  (+ `({part})` nếu nhiều phần) rồi hashtag của tài khoản ở dòng dưới.
- AI dùng OpenAI-compatible: global config trước, field trống fallback cột AI cũ
  của tài khoản; lỗi thì fallback caption fixed/title/hashtag.
- Caption AI có cấu trúc: title YouTube dòng đầu, nội dung AI (hashtag) dòng
  dưới; `({part})` gắn vào dòng title.
- Hashtag vẫn lấy từ tài khoản TikTok.
- Không còn dùng `caption_include_title`, `caption_include_hashtags`,
  `hashtag_source` cho flow tài khoản; các field này chỉ còn trong Settings cũ.
- Các cột `caption_text`, `ai_api_*` trong `tiktok_accounts` là legacy, không còn
  hiện trong dialog tài khoản; chỉ dùng làm fallback khi Cài đặt chung trống.

## 7. Pipeline song song

`PipelineOrchestrator` spawn 3 nhóm worker thread, mỗi nhóm claim riêng loại job:

- `max_download_workers` — mặc định **2**
- `max_process_workers` — mặc định **1**
- `max_upload_workers` — mặc định **1**

Chỉnh ở **Cài đặt → Hệ thống** (1–10). Worker count chỉ đọc lúc `start()`;
đổi xong phải Dừng → Bắt đầu. Các video khác nhau có thể overlap tải/xử lý/đăng;
thứ tự từng video vẫn download → process → upload.

- `failed_job_policy` (**Cài đặt → Hệ thống → Khi job lỗi**): `retry` = tự thử
  lại job lỗi có backoff đến `max_attempts` (mặc định 3); `manual` =
  `manual_action`, job nằm ở **Cần xử lý** chờ Thử lại/Bỏ qua; `skip` =
  `cancelled` rồi worker chạy job kế. Cả 3 đều ghi `job_events` WARNING/ERROR
  và không chặn các job khác. Lỗi `ManualAction` luôn dừng job để chờ người
  dùng, không theo cờ này. Riêng job `download` lỗi yêu cầu cookie YouTube
  (`Sign in to confirm`, `--cookies`, file cookie thiếu) luôn dừng ngay ở
  `failed`, không retry dù chọn `retry`.

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
- Chuông lặp theo `notification_bell_seconds` (mặc định 15 giây, sửa ở Cài đặt
  chung), nút **Tắt chuông** ở status bar khi đang kêu.
- Panel Trạng thái hoạt động hiện số job đang chạy và đang chờ của từng tầng,
  ví dụ `Tải video: Đang chạy (1) · 2 chờ`.
- Mọi thời gian UI hiển thị theo **UTC+7** (`format_local_time`,
  `local_today`). KPI hôm nay và biểu đồ 7 ngày cũng theo UTC+7.
- Giao diện (2026-11-15): bảng màu sáng/tối làm lại theo token (`theme/tokens.py`),
  QSS phủ đủ combobox popup, checkbox, progress, menu, tooltip, splitter; chữ
  tiêu đề trang 20pt, tiêu đề trong card 12pt; nav active có vạch nhấn; card,
  bảng, ô nhập có focus/hover/disabled rõ ràng. Callback theme: badge trạng thái
  và màu tầng pipeline dùng QSS property (`statusKind`, `stageState`) nên đổi
  màu đúng theo theme.
- Tổng quan: nút **Tạm dừng**, **Kill job** (đỏ, xác nhận trước khi dừng job
  đang chạy), **Bắt đầu**.
- Trang **Hàng đợi job**: bảng chỉ đọc toàn bộ `jobs` (mã, loại, video, trạng
  thái, lần thử/thử tối đa, tạo lúc, cập nhật, lỗi), mới nhất trước, màu trạng
  thái Lỗi/Cần xử lý/Đã hủy/Hoàn tất; `Làm mới` giữ trang hiện tại, mở lại tab
  tự refresh.
- UX: `Ctrl+1..8` chuyển trang, `Ctrl+R` làm mới trang hiện tại (tooltip nav ghi
  phím tắt); con trỏ chờ khi Start/Pause; toast xếp chồng + fade-in 160ms, tối đa
  rộng 480px; phụ đề trang tự xuống dòng; bảng Nhật ký tự ngắt dòng và giãn chiều
  cao dòng, cột nội dung co giãn theo cửa sổ.
- Panel phải **Trạng thái hoạt động**:
  - Refresh 1 giây; 3 tầng Tải/Xử lý/Đăng.
  - Kéo vách ngăn để co kéo độ rộng (tối thiểu 240px), ghi nhớ kích thước.
  - Đang chạy: progress + danh sách **mỗi task 1 dòng** (video, kênh, tài khoản,
    ETA riêng); ETA dựa median 10 job hoàn tất gần nhất cùng loại.
  - Chờ/vàng, hoàn tất ✓ xanh, lỗi ✕ đỏ, cần can thiệp !
  - 20 hoạt động gần nhất dạng card nền xen kẽ; giờ xanh `#2563EB`, nội dung
    weight 600.
  - Nút topbar **Hoạt động** ẩn/hiện, ghi nhớ trạng thái.
- Cỡ chữ hiển thị: nút **A− / A+** ở topbar, 8–16pt (mặc định 10pt), phóng
  theo tỉ lệ toàn bộ font trong QSS và chiều cao dòng bảng; ghi nhớ ở
  `QSettings` (`ui/font_pt`). Trang Nhật ký tự giãn dòng theo nội dung/font.
- Bảng:
  - `CopyableTable` cho video/kênh/queue/logs; click chọn ô; Ctrl+C (hoặc chuột
    phải → **Sao chép**) copy ô/vùng chọn. Hover ô hiện tooltip đúng giá trị ô
    (không còn tooltip hướng dẫn chung của cả bảng).
  - Kéo chuột luôn là chọn vùng ô như bảng thường (không bị lớp phủ chặn).
    Nháy đúp trong ô hoặc chuột phải → **Bôi đen chữ trong ô** mới mở editor
    QTextEdit chỉ đọc phủ đúng ô (nền theo theme/zebra, xuống dòng như ô),
    Ctrl+C copy phần bôi đen; không bôi đen thì copy toàn bộ nội dung ô.
    `Esc` hoặc click chỗ khác để đóng editor.
  - Bảng Kênh có 6 cột cuộn ngang bình thường (không đóng băng cột Thao tác).
  - Kéo cột, line lưới, màu xen kẽ (light `#FFFFFF/#EDF1F7`, dark
    `#131D2E/#1B2739`), hover cả dòng (light `#E6EEFF`, dark `#2B3E5C`).
  - Phân trang client-side mặc định **100 dòng/trang**, chọn 20/50/100.
  - Trang Nhật ký **không auto refresh**; có nút **Làm mới**, giữ trang hiện tại.
    Chỉ reset về trang 1 khi đổi search hoặc mức độ. Panel **Hoạt động** vẫn
    refresh 1 giây.
- Chuột không đổi giá trị `QSpinBox/QDoubleSpinBox/QComboBox` (wheel guard
  trong `main_window.run`).
- Cài đặt → File cookie YouTube: chọn file Netscape `.txt`; nếu có file thì
  download luôn dùng `cookiefile`, không phụ thuộc `use_vpn_for_youtube`.
  File không tồn tại thì không lưu và báo lỗi.
- Cài đặt → **Chất lượng tải video**: `best` (mọi độ phân giải, file lớn nhất),
  `av1_1080` (AV1 1080p, nhỏ nhất), `h264_1080` (H.264 1080p, tương thích),
  `av1_720` (AV1 720p, nhẹ nhất). Đầu ra cuối vẫn 1080×1920.
- Kênh theo dõi: nút Sửa/Tạm dừng/Xóa nằm ở cột cuối **cùng bảng**; Làm mới
  cạnh Thêm kênh. Thêm/bật lại kênh tự gọi refresh WebSub subscription ngay.
- Log job có chi tiết:
  - `Bắt đầu tải video: {title} | Tài khoản TikTok: {account} | Kênh: {channel}`
  - `Bắt đầu xử lý video: ... | Tài khoản TikTok: ... | Kênh: ...`
  - `Bắt đầu đăng TikTok: ... | Tài khoản TikTok: ... | Kênh: ...`
  - `Hoàn tất tải video/xử lý video/đăng TikTok: ...`
  - Tài khoản hiển thị là tài khoản test trong payload, nếu không có thì tài
    khoản route của kênh YouTube.
  - `Xử lý xong phần N/M: {tên file} — sẵn sàng đăng TikTok | Tài khoản TikTok:
    {account} ({đường dẫn})`
    ghi ngay sau mỗi phần render xong, không chờ đủ các phần.
  - `AI đã sinh caption/hashtag cho video: {title}` rồi xuống dòng caption
    hoàn chỉnh; log trang Nhật ký tự giãn chiều cao dòng khi có xuống dòng.

## 10. WebSub / Cloudflare

- WebSub là **cách duy nhất** phát hiện video mới; không còn RSS fallback.
- WebSub server mặc định `127.0.0.1:8000`, callback `/webhook`.
- Cloudflare Tunnel trỏ `http://127.0.0.1:8000`; domain public bắt buộc trong Cài đặt.
- WebSub push chỉ ảnh hưởng độ trễ phát hiện video, không ảnh hưởng tốc độ tải.
- Một luồng `websub-scheduler` renew subscription theo `websub_renew_interval_seconds`
  (mặc định 3600s); lần renew đầu sau mỗi lần Bắt đầu dùng `force=True`.
- `WebSubService.renew_due` có lock + chống gửi trùng trong 300s cho cùng kênh/callback.
- Kênh mới: route dùng ngay; WebSub subscription được refresh ngay khi app đang chạy,
  YouTube xác minh async. Nếu lỗi/không chắc: Dừng → Bắt đầu để force renew.
- Xóa/tạm dừng kênh chưa unsubscribe WebSub; subscription cũ có thể còn đến hết hạn.
  Notification từ kênh đang tạm dừng (hoặc không còn trong DB) bị bỏ qua: không tạo
  `media_items`, không enqueue download/process.

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
    pages/                 # dashboard/accounts/videos/channels/queue/jobs/logs/settings
    dialogs/               # AccountDialog, ChannelDialog
    services/repository.py # SQL cho UI, format UTC+7, pipeline_activity/ETA
    theme/tokens.py        # QSS light/dark
CLAUDE.md                  # hướng dẫn ngắn tự nạp mỗi phiên
tests/test_pipeline.py     # 90 tests
```

`mmo_app/ui/` đã commit trong `f243dcc` trên nhánh
`feat/pyside6-pipeline`; nhánh đã có trên `origin`. Sau commit đó còn nhiều
file sửa chưa commit (CUDA, caption global, download quality, cookie, UI table).
`dist_preview/` đã nằm trong `.gitignore`.

## 12. Việc còn dở / rủi ro

1. **Selector TikTok** đang bám text; cần chuyển `data-e2e`/`data-testid`/class TUX
   trước, text làm fallback. Xem `NOTES_SELECTOR_LOCALIZATION.md`.
2. `mmo_app/desktop.py` là UI Tkinter cũ, còn field CDP; không dùng. Cân nhắc xóa.
3. `HUONG_DAN_SU_DUNG.md`, `FLOW_UPLOAD_TIKTOK.md`,
   `LUONG_HOAT_DONG_CONG_NGHE_DEV.md`, `README.md` còn mô tả RSS/CDP; chưa cập
   nhật WebSub-only và GPM.
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
13. yt-dlp 2026 cần JS runtime cho challenge `n` của YouTube. App tự dùng
    `deno`/`node`/`bun` nếu có trong PATH (`DownloadHandler.js_runtime_options`).
    Máy build cần runtime này, hoặc bundle vào `tools/runtime`.
14. CUDA cần ffmpeg có `scale_cuda`, `pad_cuda`, `overlay_cuda` và driver NVIDIA
    tương thích; bản không hỗ trợ sẽ fallback CPU. Đo trên RTX 3050 Laptop:
    ~3–4.7x nhanh hơn CPU cho video 30s, file lớn hơn do CQ 23.
15. Không chain hai `scale_cuda` liên tiếp: build ffmpeg hiện tại cho frame xanh.
    Zoom rộng/cao lệch dùng 1 `scale_cuda`, `hwdownload`, scale CPU, crop/pad,
    rồi `hwupload_cuda` (`cuda_video_filter`).
16. `caption_text` và `ai_api_*` trong `tiktok_accounts` là legacy; dialog tài
    khoản không còn sửa các cột này. Caption/AI lấy từ Settings trước, cột tài
    khoản chỉ fallback.
17. Máy hiện chưa có AI API key; chọn source AI mà thiếu key sẽ fallback caption
    fixed/title và ghi warning.
18. Render có watchdog thời gian: CUDA tối thiểu 300s, CPU tối thiểu 900s
    (theo độ dài phần). Quá hạn thì kill ffmpeg, xóa file mp4 dở và fallback
    CPU; không còn treo tới timeout 7200s khi driver/NVENC đứng (0% CPU,
    file thiếu `moov`).
19. Worker tự `recover_expired_leases()` mỗi 30s khi pipeline chạy; dashboard
    không tính job có lease đã hết hạn là "Đang chạy" (hiện "Đang chờ"). Tắt/mở
    lại app không còn job kẹt `leased` vĩnh viễn; luôn kiểm tra/kill
    `ffmpeg.exe` mồ côi trước khi chạy lại job đang dở.

## 13. Quyết định không nên phá

- UI chỉ là tầng hiển thị; SQL gom ở `UIRepository`, logic nghiệp vụ ở core.
- Mọi timestamp DB là UTC; UI đổi UTC+7, không lưu giờ local.
- Job idempotency key phải giữ ổn định; test job dùng key riêng để chạy lặp.
- Upload luôn qua GPM profile; không quay lại CDP.
- Task test `is_test` tuyệt đối không được đăng.
- Tài khoản inactive vẫn dùng config để cắt; chỉ chặn GPM/đăng.
- File video đặt tên `{title}_{N}.mp4`; caption `{title} ({N})` khi nhiều phần.
- Chỉ WebSub phát hiện video mới; không thêm RSS fallback hoặc scheduler renew thứ hai.
- Caption cố định + AI cấu hình chung ở Settings; tài khoản chỉ chọn nguồn caption
  (`''`/`fixed`/`ai`) + hashtag. AI sinh một lần trước khi render.
- Download mặc định `best`, nhưng phải giữ các lựa chọn `av1_1080`, `h264_1080`,
  `av1_720` cho người cần file nhỏ.
- CUDA: không chain `scale_cuda`; zoom lệch phải qua CPU crop/pad; lỗi fallback CPU.
- Không thêm hook nặng vào pipeline chỉ để phục vụ UI; panel đọc DB định kỳ.

## 14. Checklist phiên mới

1. Đọc `CLAUDE.md` + file này + 3 file `NOTES_*`/`SPEC_*`.
2. `git status` để biết phần chưa commit; nhánh hiện tại `feat/pyside6-pipeline`.
3. Chạy `unittest discover -s tests -v`; kỳ vọng **90 tests OK** trở lên.
4. Nếu sửa pipeline, kiểm tra cả job download/process/upload và test video.
5. Nếu sửa UI bảng, giữ chọn/copy một phần chữ, kéo cột, xen kẽ, phân trang
   100 dòng/trang, line lưới.
6. Nếu sửa caption, nhớ `{title} ({part})`, không dùng chữ "Phần" trong caption;
   tên file vẫn `{title}_{N}.mp4`; AI/hashtag sinh một lần trước render.
7. Nếu sửa account active, giữ quy tắc: inactive vẫn cắt theo config, chỉ chặn
   mở GPM/đăng.
8. Nếu sửa encoder/download/cookie, đọc mục 5, 9, 10 và 12 trước.
9. Chạy app thật để kiểm tra UI:
   `.\.venv\Scripts\python.exe main.py`
