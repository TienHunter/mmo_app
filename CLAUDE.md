# MMO App — hướng dẫn làm việc

Trước khi sửa code, đọc `TRANG_THAI_DU_AN.md`. Spec chi tiết nằm ở
`SPEC_VIDEO_PROCESSING_RULES.md`, `NOTES_CAPTION_COMPOSE.md`,
`NOTES_SELECTOR_LOCALIZATION.md`.

## Môi trường và lệnh

- Python 3.12. Chỉ dùng `.venv` trong repo, không dùng Python hệ thống.
- Chạy app: `.\.venv\Scripts\python.exe main.py`
- Kiểm tra cú pháp: `.\.venv\Scripts\python.exe -m compileall -q mmo_app`
- Test: `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`
- Entry point: `main.py` → `mmo_app/ui/main_window.py`. `mmo_app/desktop.py` là
  UI Tkinter cũ, không phải entry point.

## Bất biến phải giữ

- DB lưu timestamp UTC. UI chỉ đổi sang UTC+7 khi hiển thị; không lưu giờ local.
- UI chỉ là tầng hiển thị. SQL gom ở `UIRepository`; nghiệp vụ ở
  `application.py` / `services.py`.
- Job idempotency key phải ổn định. Job test dùng key riêng để chạy lặp.
- Upload TikTok luôn qua GPM profile; không quay lại CDP.
- Task `is_test=1` tuyệt đối không được đăng.
- Tài khoản TikTok inactive vẫn tải và cắt video theo đúng config; chỉ chặn
  tạo upload task, mở GPM profile và đăng bài.
- Video đặt tên `{title}_{N}.mp4`; caption trên video `{title} ({part})`.
  Không dùng chữ "Phần" trong caption.
- Nhận video mới chỉ bằng WebSub. Không thêm lại RSS fallback.
- WebSub chỉ có một scheduler renew; `renew_due()` có lock và chống gửi trùng.
  Không tạo thêm luồng renew song song.
- Caption cố định + AI (URL/key/model/system prompt) cấu hình chung ở Settings.
  Tài khoản chỉ chọn nguồn caption (`Theo Cài đặt chung` / `fixed` / `ai`) và
  hashtag. Caption AI sinh một lần khi bắt đầu process, trước khi render.
- Encoder CUDA: không chain hai `scale_cuda` (build ffmpeg hiện tại cho frame
  xanh). Zoom >100 hoặc zoom rộng/cao lệch phải `hwdownload` → crop/pad CPU →
  `hwupload_cuda`. GPU lỗi thì fallback `libx264`.
- yt-dlp 2026 cần JS runtime (`deno`/`node`/`bun`) cho challenge `n`. Cookie
  YouTube chỉ dùng khi file Netscape có đủ cookie đăng nhập; file cookie thiếu
  hoặc rỗng làm YouTube chỉ trả storyboard.

## Khi sửa code

- Sửa schema/phân trang/UI: giữ chọn ô, copy một phần chữ, kéo cột, xen kẽ,
  phân trang mặc định 100 dòng/trang.
- Sửa caption: nhớ log `Xử lý xong phần N/M ... sẵn sàng đăng`, caption AI
  hiển thị dưới title, và hashtag vẫn thuộc tài khoản.
- Sửa pipeline: kiểm tra cả download/process/upload và test video.
- Thay đổi hành vi/schema/quyết định: cập nhật `TRANG_THAI_DU_AN.md` cùng commit.
- Không commit hoặc push nếu người dùng chưa yêu cầu.
