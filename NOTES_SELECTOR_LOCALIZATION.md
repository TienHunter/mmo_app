# TODO — Làm selector TikTok không phụ thuộc ngôn ngữ/vùng

Ngày ghi chú: 2026-11-10

## Vấn đề

Luồng upload TikTok hiện dùng nhiều selector bám theo text (tiếng Anh/Việt).
Khi TikTok đổi ngôn ngữ hoặc chạy qua VPN sang nước khác, các chữ này thay đổi
và selector không còn khớp.

## Các chỗ cần sửa (trong `mmo_app/services.py`)

- Nút chọn video fallback:
  - `button:has-text("Select video")`
  - `button:has-text("Chọn video")`
  - `button:has-text("Tải video lên")`
  - `button[aria-label="Select video"]`
- Chờ form caption bằng từ khóa trong body:
  - `Uploading`, `Processing`, `Đang tải`, `Đang xử lý`
  - `Caption`, `Description`, `Mô tả`, `Hashtag`
- Tìm ô caption/description:
  - `textarea[placeholder*="caption" i]`
  - `textarea[placeholder*="description" i]`
  - `[contenteditable="true"][aria-label*="caption" i]`
  - `[contenteditable="true"][aria-label*="description" i]`
  - `[contenteditable="true"][data-e2e*="caption" i]`
- Tìm nút Post:
  - `get_by_role("button", name=re.compile(r"^(Post|Đăng)$"))`
- Popup xác nhận "Continue to post?":
  - `name=re.compile(r"^(Post now|Đăng ngay|Tiếp tục đăng)$")`
  - xpath `@title="Continue to post?"`
  - xpath `normalize-space()="Continue to post?"` / `normalize-space()="Post now"`
  - `div.TUXModal[role="dialog"]:has-text("Continue to post?") button.TUXButton--primary:has-text("Post now")`
  - `button:has-text("Post now")`
- Chẩn đoán lỗi upload page:
  - Đăng nhập: `Log in`, `Login`, `Sign up`, `Đăng nhập`, `Đăng ký`
  - Captcha/xác minh: `captcha`, `verification`, `verify`, `xác minh`, `checkpoint`, `security check`
  - Trang lỗi: `Something went wrong`, `Try again`, `Không thể`, `Lỗi`

## Hướng sửa dự kiến

1. Ưu tiên selector không phụ thuộc text: `data-e2e`, `data-testid`, class TUX,
   role + regex đa ngôn ngữ.
2. Text thuần chỉ dùng làm fallback cuối cùng.
3. Với các bước chẩn đoán, đổi sang kiểm tra URL, DOM/selector cấu trúc, và chỉ
   dùng text để ghi log cho người dùng.

## Các selector đã ổn (không cần sửa)

- `input[type="file"]`, `input[accept*="video"]`
- iframe `src*="creator"`, `src*="upload"`, `src*="studio"`
- `.mention-list-popover [role="option"]`, `.hashtag-suggestion-item`
- `[data-e2e="select_video_button"]`, `[data-e2e="select_video_container"]`
