# Flow upload TikTok

Tài liệu này mô tả chi tiết luồng upload TikTok hiện tại của MMOApp, bao gồm cách app mở profile/CDP, chờ element, selector đang dùng, timeout và các điểm fallback.

## 1. Điều kiện đầu vào

Một job upload được tạo sau khi video đã được xử lý xong. Bản ghi upload cần có:

- `path`: đường dẫn file video đã tách trên máy.
- `caption`: caption đưa lên TikTok. Hiện tại caption chỉ lấy danh sách hashtag đã cấu hình cho kênh TikTok.
- `mode`: `gpm` hoặc `cdp`.
- `endpoint`: CDP URL nếu dùng chế độ CDP thủ công.
- `gpm_profile_id`: Profile ID nếu dùng GPM Login.

Nếu thiếu CDP URL hoặc thiếu GPM Profile ID theo mode đang chọn, job sẽ chuyển sang trạng thái cần thao tác thủ công.

## 2. Mở trình duyệt TikTok

### 2.1. Chế độ GPM

Nếu tài khoản TikTok chọn mode `gpm`, app gọi GPM Login API để mở profile:

- API URL mặc định: `http://127.0.0.1:9495/api/v1`
- Profile lấy từ ô `GPM Profile ID` của layout TikTok.
- Timeout gọi GPM API: 60 giây.
- Sau khi GPM trả CDP endpoint, app kiểm tra cổng CDP có sống không bằng `/json/version`.
- Thời gian chờ CDP sẵn sàng: 60 giây.

Nếu GPM báo profile đang mở:

- App thử dò CDP port của profile đang chạy bằng command line process trên Windows.
- Nếu bật option tự reclaim profile, app gửi lệnh stop profile rồi mở lại.
- Nếu vẫn không lấy được CDP, job báo lỗi thủ công để người dùng đóng/mở lại profile.

### 2.2. Chế độ CDP thủ công

Nếu tài khoản TikTok chọn mode `cdp`, app dùng:

- CDP URL trong layout TikTok.
- Nếu layout không nhập, dùng CDP URL global trong tab cấu hình.

Chrome phải được mở sẵn bằng remote debugging, ví dụ:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="D:\mmo\chrome_profiles\tiktok_1"
```

CDP URL tương ứng nhập vào app:

```text
http://127.0.0.1:9222
```

## 3. Kết nối Playwright qua CDP

App dùng Playwright để kết nối vào Chrome đang mở:

- Log: `TikTok: đang kết nối Chrome/CDP`
- Hàm kết nối retry trong tối đa 60 giây.
- Mỗi lần lỗi kết nối sẽ chờ 1 giây rồi thử lại.
- Nếu hết 60 giây vẫn không kết nối được, job báo lỗi.

Nếu GPM trả `ws://localhost:<port>/devtools/browser/...`, app chuyển sang `http://127.0.0.1:<port>` khi có thể. Mục đích là tránh lỗi Playwright không thể transfer file lớn hơn 50MB khi CDP bị hiểu là remote websocket.

## 4. Mở trang upload TikTok

Sau khi kết nối CDP:

1. App lấy browser context đầu tiên.
2. Nếu context chưa có page thì tạo page mới.
3. Mở URL:

```text
https://www.tiktok.com/upload?lang=en
```

Thông số mở trang:

- `wait_until="domcontentloaded"`
- Log trước khi mở: `TikTok: đang mở trang upload`
- Log sau khi mở: `TikTok: đã mở trang upload`

## 5. Chọn file video

Đây là bước quan trọng nhất vì TikTok có thể đổi UI, có iframe, hoặc input file bị ẩn.

### 5.1. Ưu tiên tìm `input[type=file]`

App luôn ưu tiên tìm input file trực tiếp, kể cả khi input đang hidden. Lý do: Playwright có thể set file trực tiếp vào `input[type=file]`, không cần click nút chọn file.

Timeout tổng cho bước tìm và set file:

```text
45 giây
```

Log:

```text
TikTok: đang tìm input[type=file]
TikTok: đã tìm thấy input[type=file]
TikTok: đang set file <path>
TikTok: đã set file <filename>
TikTok: đang chờ form caption
TikTok: đã thấy form caption/upload details
```

Selector input file trên page chính:

```text
input[type="file"][accept*="video"]
input[accept*="video"]
input[type="file"]
```

Selector input file trong iframe:

```text
iframe
iframe[src*="creator"]
iframe[src*="upload"]
iframe[src*="studio"]
```

Với mỗi iframe, app tìm:

```text
input[type="file"]
input[accept*="video"]
```

Cách chờ:

- App tạo danh sách candidate từ page chính và iframe.
- Mỗi candidate chờ state `attached`, không bắt buộc `visible`.
- Timeout mỗi candidate: tối thiểu 3 giây, tối đa 10 giây.
- Tổng deadline vẫn là 45 giây.

Sau khi tìm thấy input:

- Nếu `path` là đường dẫn tuyệt đối nhưng file không tồn tại, báo lỗi ngay.
- Gọi `set_input_files(<path>)`.
- Nếu set file thất bại, báo lỗi rõ: đã tìm thấy input nhưng set file thất bại.

### 5.2. Fallback qua nút Select video

Nếu không tìm được `input[type=file]`, app mới fallback sang click nút chọn file và bắt file chooser.

Selector nút:

```text
button[aria-label="Select video"]
button:has-text("Select video")
button:has-text("Chọn video")
button:has-text("Tải video lên")
[data-e2e="select_video_button"]
[data-e2e="select_video_container"] button
```

Cách chờ:

- Tổng deadline vẫn theo timeout 45 giây.
- Với mỗi selector, chờ nút visible tối đa 10 giây hoặc thời gian còn lại.
- Dùng `page.expect_file_chooser()` tối đa 10 giây.
- Click nút bằng cơ chế human click.
- Gọi `chooser.value.set_files(<path>)`.

Nếu trong lúc fallback app phát hiện form caption đã hiện, app coi như upload file đã thành công và đi tiếp.

## 6. Chờ form caption/upload details

Sau khi set file, app chờ TikTok chuyển khỏi màn chọn file sang form upload detail.

Timeout:

```text
45 giây
```

Logic chờ:

- Mỗi vòng kiểm tra editor caption trong 1 giây.
- Nếu chưa thấy editor, đọc text trong `body` trong 1 giây.
- Nếu body có các từ sau thì coi như form/tiến trình upload đã hiện:

```text
Uploading
Processing
Đang tải
Đang xử lý
Caption
Description
Mô tả
Hashtag
```

- Mỗi vòng lặp nghỉ 500ms.
- Nếu hết 45 giây vẫn không thấy form, báo lỗi: TikTok đã nhận file nhưng chưa hiển thị form caption/upload details.

## 7. Tìm ô caption/description

Sau bước chọn file, app tiếp tục chờ ô caption/hashtag.

Timeout:

```text
20 phút
```

Log:

```text
TikTok: đang chờ ô caption/hashtag
TikTok: đã thấy ô caption, bắt đầu nhập hashtag
```

Selector trên page chính:

```text
[contenteditable="true"][role="textbox"]
[contenteditable="true"][aria-label*="caption" i]
[contenteditable="true"][aria-label*="description" i]
[contenteditable="true"][data-e2e*="caption" i]
[contenteditable="true"]
textarea[placeholder*="caption" i]
textarea[placeholder*="description" i]
```

Selector trong iframe:

```text
iframe
iframe[src*="creator"]
iframe[src*="upload"]
iframe[src*="studio"]
```

Với mỗi iframe, app tìm lại toàn bộ selector editor ở trên.

Cách chờ:

- Mỗi selector chờ state `visible`.
- Timeout mỗi selector tối đa 10 giây hoặc thời gian còn lại.
- Nếu page chính không có, app thử trong iframe.

## 8. Click theo kiểu human click

Các thao tác click chính, ví dụ click editor, click hashtag suggestion, click Post, đều dùng helper human click.

Luồng human click:

1. Chờ element `visible`, timeout mặc định 10 giây.
2. Scroll element vào viewport nếu cần.
3. Lấy bounding box.
4. Nếu có bounding box:
   - Move chuột tới tâm element với `steps=8`.
   - Chờ 120ms.
   - Click bằng `page.mouse.click`.
5. Nếu không lấy được bounding box hoặc mouse click lỗi:
   - Fallback sang `locator.click()`.

Như vậy app vẫn dùng DOM selector làm nguồn chính xác, nhưng thao tác click có chuyển động chuột để giống người dùng hơn.

## 9. Nhập hashtag caption

Caption TikTok hiện tại chỉ lấy hashtag từ cấu hình TikTok account/layout.

Trước khi nhập:

- App click vào editor.
- `Control+A`
- `Backspace`
- Tách hashtag bằng regex:

```text
#[^\s#]+
```

Nếu không có hashtag:

```text
TikTok: không có hashtag để nhập
```

Với từng hashtag:

1. Log: `TikTok: đang nhập hashtag #...`
2. Gõ hashtag vào editor với delay 35ms/ký tự.
3. Chờ popup gợi ý hashtag và click item đầu tiên.
4. Nếu còn hashtag tiếp theo, gõ thêm dấu cách với delay 20ms.

Selector popup hashtag:

```text
.mention-list-popover [role="option"].focused
.mention-list-popover [role="option"]
.hashtag-suggestion-item.focused
.hashtag-suggestion-item
xpath=//div[contains(@class,"mention-list-popover")]//*[@role="option"][1]
```

Timeout chọn hashtag suggestion:

```text
5 giây cho mỗi candidate
```

Nếu thấy suggestion:

```text
TikTok: đã chọn hashtag #...
```

Nếu không thấy popup/suggestion:

```text
TikTok: đã nhập hashtag #... nhưng không thấy popup gợi ý
```

Trong trường hợp không click được candidate nào, app thử fallback bằng phím `Enter`.

Lưu ý quan trọng: TikTok chỉ tính hashtag đúng nghĩa khi chọn từ popup gợi ý. Vì vậy nếu log báo không thấy popup gợi ý, hashtag có thể chỉ bị TikTok coi như text thường.

## 10. Chờ nút Post và đăng

Sau khi nhập hashtag:

```text
TikTok: đã nhập hashtag, đang chờ nút Post
```

App tìm button theo role:

```text
button name: Post hoặc Đăng
```

Regex:

```text
^(Post|Đăng)$
```

Timeout chờ nút Post visible:

```text
20 phút
```

Sau đó:

```text
TikTok: đang bấm Post
```

App click nút Post bằng human click.

## 11. Popup xác nhận “Continue to post?”

Sau khi bấm Post, TikTok đôi khi hiện popup:

```text
Continue to post?
We're still checking your video for potential issues...
```

App đã xử lý popup này bằng cách tìm và click `Post now`.

Selector:

```text
button role name: Post now hoặc Đăng ngay hoặc Tiếp tục đăng
xpath=//div[contains(@class,"TUXModal") and @role="dialog" and @title="Continue to post?"]//button[.//*[normalize-space()="Post now"]]
xpath=//div[contains(@class,"common-modal-confirm-modal") and .//*[normalize-space()="Continue to post?"]]//button[.//*[normalize-space()="Post now"]]
div.TUXModal[role="dialog"]:has-text("Continue to post?") button.TUXButton--primary:has-text("Post now")
button:has-text("Post now")
```

Timeout:

```text
3 giây cho mỗi candidate
```

Nếu thấy popup thì click `Post now`. Nếu không thấy popup thì bỏ qua và tiếp tục chờ kết quả đăng.

## 12. Chờ upload hoàn tất

Sau khi bấm Post và xử lý popup nếu có, app chờ URL rời khỏi trang `/upload`.

Regex URL:

```text
^(?!.*\/upload).*$
```

Timeout:

```text
20 phút
```

Nếu URL đã rời `/upload`:

```text
TikTok: đăng video hoàn tất
```

Sau đó app cập nhật database:

- `upload_tasks.status = completed`
- `upload_tasks.posted_at = thời gian hiện tại`
- `tiktok_accounts.posts_today += 1`
- `tiktok_accounts.status = idle`
- `media_items.status = uploaded`

## 13. Chẩn đoán lỗi upload page

Nếu bước chọn file lỗi, app đọc trạng thái page để trả thông báo dễ hiểu hơn.

Các nhóm lỗi đang chẩn đoán:

### 13.1. Chưa đăng nhập

Pattern:

```text
Log in
Login
Sign up
Đăng nhập
Đăng ký
```

Thông báo:

```text
TikTok đang mở màn hình đăng nhập. Hãy đăng nhập đúng Chrome profile/CDP port rồi chạy lại job.
```

### 13.2. Captcha/xác minh

Pattern:

```text
captcha
verification
verify
xác minh
checkpoint
security check
```

Thông báo:

```text
TikTok đang yêu cầu xác minh/captcha trên Chrome profile này. Hãy xử lý thủ công trong Chrome rồi chạy lại job.
```

### 13.3. Trang upload lỗi

Pattern:

```text
Something went wrong
Try again
Không thể
Lỗi
```

Thông báo:

```text
Trang upload TikTok đang báo lỗi. Hãy tải lại trang trong Chrome CDP rồi chạy lại job.
```

### 13.4. URL login

Nếu URL chứa `/login` hoặc `login`, app báo có thể đang kết nối nhầm CDP port hoặc nhầm Chrome profile chưa đăng nhập.

### 13.5. URL upload nhưng không thấy input file

Nếu vẫn đang ở `/upload`, app báo TikTok đã mở trang upload nhưng không tìm thấy ô chọn file. Khả năng thường gặp:

- TikTok đổi giao diện.
- Ô upload nằm trong iframe chưa tải xong.
- Chrome/CDP đang treo page cũ.
- Profile đang ở trạng thái lỗi hoặc cần reload.

## 14. Timeout tổng hợp

| Bước | Timeout |
| --- | ---: |
| Gọi GPM API | 60 giây |
| Chờ GPM CDP `/json/version` sẵn sàng | 60 giây |
| Kết nối Playwright CDP | 60 giây |
| Tìm và set `input[type=file]` | 45 giây |
| Mỗi candidate input file | 3-10 giây |
| Fallback nút Select video/file chooser | tối đa 45 giây tổng, mỗi selector tối đa 10 giây |
| Chờ form caption sau khi set file | 45 giây |
| Chờ ô caption/description sau upload | 20 phút |
| Chọn suggestion cho mỗi hashtag | 5 giây/candidate |
| Human click mặc định | 10 giây |
| Chờ nút Post | 20 phút |
| Popup `Continue to post?` | 3 giây/candidate |
| Chờ URL rời `/upload` | 20 phút |

## 15. Log tiêu chuẩn trong tab Log

Một flow upload bình thường sẽ có dạng:

```text
Bắt đầu upload
GPM: đang mở profile <profile_id>
GPM: profile <profile_id> đã sẵn sàng qua CDP <cdp_url>
TikTok: đang kết nối Chrome/CDP
TikTok: đang mở trang upload
TikTok: đã mở trang upload
TikTok: đang tìm input[type=file]
TikTok: đã tìm thấy input[type=file]
TikTok: đang set file <path>
TikTok: đã set file 001.mp4
TikTok: đang chờ form caption
TikTok: đã thấy form caption/upload details
TikTok: đang chờ ô caption/hashtag
TikTok: đã thấy ô caption, bắt đầu nhập hashtag
TikTok: đang nhập hashtag #...
TikTok: đã chọn hashtag #...
TikTok: đã nhập hashtag, đang chờ nút Post
TikTok: đang bấm Post
TikTok: đăng video hoàn tất
```

## 16. Các điểm dễ gây chậm

Các bước có thể nhìn “chậm” trên UI Chrome nhưng là do app đang chờ an toàn:

- Chờ TikTok render iframe/upload widget.
- Chờ `input[type=file]` attached thay vì chỉ nhìn nút Select video.
- Chờ form caption sau khi file được nhận.
- Chờ TikTok xử lý video trước khi nút Post sẵn sàng.
- Chờ popup hashtag để chọn đúng hashtag chứ không chỉ nhập text.
- Chờ URL rời `/upload` để xác nhận post đã hoàn tất.

Nếu muốn debug, hãy nhìn log theo mốc ở mục 15 để biết app đang đứng ở bước nào.
