# Ghi chú — Compose description TikTok + generate hashtag bằng AI

Ngày: 2026-11-10

## Đã làm

- Thêm cấu hình trong `Settings`:
  - `caption_include_title`, `caption_include_hashtags`
  - `hashtag_source` (`fixed` / `ai`)
  - `ai_api_url`, `ai_api_key`, `ai_model`, `ai_system_prompt`
- Thêm UI trong tab Cài đặt → Đăng TikTok cho các cấu hình trên.
- `ProcessHandler._route_uploads` giờ compose caption:
  - Dòng title: tên video YouTube + `(Part N)` khi có nhiều phần.
  - Dòng hashtag phía dưới (cố định hoặc generate bằng AI).
  - AI gọi OpenAI-compatible `chat/completions`, dùng system prompt + title,
    fallback về hashtag cố định nếu API lỗi.

## Đã hoàn thiện (2026-11-11)

- `UploadHandler._fill_tiktok_caption` giờ gõ đủ description: text/title trước,
  xuống dòng bằng Enter, rồi từng hashtag kèm click suggestion.
- Caption được compose theo từng tài khoản TikTok (`caption_mode`, `caption_text`):
  `fixed` dùng caption cố định, `ai` gọi OpenAI-compatible để sinh caption; lỗi AI
  thì fallback về caption cố định/hashtag.
- Quy tắc chi tiết nằm trong `SPEC_VIDEO_PROCESSING_RULES.md`.
