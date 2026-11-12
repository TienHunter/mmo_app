# Spec — Quy tắc xử lý video theo kênh TikTok

Ngày: 2026-11-11

File này khoá các yêu cầu để thi công khi môi trường chạy lệnh hoạt động lại.

## 1. Luồng kích hoạt

Khi có thông báo video mới từ kênh YouTube:

1. Tải video về.
2. Phát chuông thông báo desktop (giả định: dùng `winsound` hoặc thông báo hệ thống).
3. Xử lý video theo cấu hình của kênh TikTok tương ứng.
4. Sau khi cắt xong, thêm vào tab "Hàng đợi & lịch đăng" với trạng thái `pending`.

## 2. Cấu hình theo từng kênh TikTok

Mỗi `tiktok_accounts` cần thêm các trường:

- `speed` (REAL, mặc định 1.0): tăng/giảm tốc độ video.
- `zoom_width_percent` (INTEGER, mặc định 100): zoom chiều rộng.
- `zoom_height_percent` (INTEGER, mặc định 100): zoom chiều cao.
- `caption_mode` (TEXT, `fixed` hoặc `ai`): caption có sẵn hay sinh bằng AI.
- `caption_text` (TEXT): caption cố định nếu `caption_mode = fixed`.
- `output_dir` (TEXT): thư mục chứa video đã cắt của kênh đó.
- `notification_sound` (TEXT): đường dẫn file âm thanh chuông; để trống thì dùng
  âm thanh mặc định của hệ thống.

Giả định: zoom width/height độc lập nhau, khoảng 0–300, mặc định 100.

## 3. Quy tắc cắt video

- Áp dụng speed trước: `effective_duration = source_duration / speed`.
- Cắt dựa trên `effective_duration`:

| Thời lượng (sau speed) | Số phần |
| --- | --- |
| dưới 5 phút | 1 (không cắt) |
| 5 – 8 phút | 2 |
| 8 – 15 phút | 3 |
| 15 – 18 phút | 4 |
| trên 18 phút | 5 |

- Tối đa 5 phần.
- Các phần gần bằng nhau nhưng **không giống hệt nhau**: áp dụng lệch ±3–5%
  so với độ dài trung bình, phần cuối hấp thụ phần dư; không tạo phần cuối
  quá ngắn (giữ trong khoảng 90–110% độ dài trung bình).

## 4. Render video

- Tỷ lệ đầu ra cố định `9:16`.
- Chất lượng đầy đủ (giữ chất lượng gốc, không giảm bitrate quá mức).
- Nền canvas đen, scale + pad để giữ toàn bộ hình gốc, không méo.
- Áp dụng speed bằng `atempo`.
- Zoom width/height riêng: scale video về canvas trước, sau đó scale độc lập
  theo từng chiều rồi crop/pad theo canvas (chiều cao 140% sẽ cao lên thật).

## 5. Caption overlay trên video

- Caption chèn trên video:
  - Nền chữ: hình chữ nhật trắng, bo góc nhẹ.
  - Nền ôm sát chữ, có padding nhỏ (giống option caption của CapCut).
  - Chữ màu đen.
  - Nền video phía sau là đen.
- Vị trí: hiển thị **phía trên video trong vùng nền đen**, không đè lên hình;
  giữ khoảng an toàn phía trên (16% chiều cao canvas) để không đè lên thanh
  "For You" của TikTok.
- Padding giữa chữ và nền trắng: **10px**.
- Font: **Arial Bold** (`arialbd.ttf`), fallback Arial.

## 6. Thư mục và tên file

- Mỗi kênh TikTok có `output_dir` riêng.
- Video cắt xong lưu vào `output_dir` của kênh đó.
- Tên file: `{tên video YouTube}_{N}`, sanitize ký tự không hợp lệ Windows.

## 7. Caption khi đăng TikTok

- Mỗi kênh TikTok chọn:
  - `fixed`: dùng `caption_text` có sẵn.
  - `ai`: sinh caption/hashtag từ AI (system prompt + title video, endpoint
    OpenAI-compatible đã thêm ở `Settings`).

## 8. Các file/functions cần sửa (khi shell hoạt động)

- `mmo_app/schema.sql` + `database.py`: thêm cột cho `tiktok_accounts`.
- `mmo_app/settings.py`: giữ AI config global đã có.
- `mmo_app/services.py`:
  - `ProcessHandler._processing_options`: lấy option từ tài khoản TikTok qua route.
  - `ProcessHandler.run`: thay logic cắt theo threshold ở mục 3.
  - `ProcessHandler._render`: thêm zoom width/height riêng + caption box trắng/chữ đen.
  - Thông báo chuông sau khi download xong.
- `mmo_app/ui/dialogs/account_dialog.py` + `accounts_page.py`: thêm field cấu hình.
- `mmo_app/ui/services/repository.py`: đọc/ghi các field mới.
- Kiểm tra lại `tests/test_pipeline.py` vì logic cắt đổi.

## 9. Đã chốt

1. Vị trí caption overlay: phía trên video, sau khi zoom + chuyển tỷ lệ, padding 5–10px.
2. Chênh lệch thời gian giữa các phần: lệch ±3–5% so với trung bình, phần cuối hấp thụ phần dư,
   không tạo phần cuối ngắn hơn ~50% độ dài trung bình (sai lệch ~10%).
3. Chuông thông báo: mỗi tài khoản TikTok có trường chọn file âm thanh;
   nếu để trống thì dùng âm thanh mặc định của hệ thống.
