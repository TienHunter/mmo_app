# Hướng dẫn sử dụng MMO App

MMO App là ứng dụng desktop thống nhất cho toàn bộ luồng:

```text
Theo dõi kênh YouTube → tải video mới → xử lý video → upload lên TikTok
```

Người dùng chỉ cần mở một ứng dụng duy nhất: `MMOApp.exe`.

---

## 1. Cài đặt và mở ứng dụng

### Bản dành cho người dùng

1. Giải nén file:

   ```text
   MMOApp-Windows-x64.zip
   ```

2. Mở thư mục sau khi giải nén.

3. Chạy:

   ```text
   MMOApp.exe
   ```

Không cần chạy riêng `youtube_tracker`, `video_processor` hoặc `tiktok_auto_upload`. Các phần đó đã được gom vào MMO App như module nội bộ.

### Lưu ý khi chạy lần đầu

- Windows có thể hỏi xác nhận vì app chưa ký số. Chọn cho phép chạy nếu bạn tin nguồn phát hành.
- Nếu dùng WebSub, tường lửa Windows có thể hỏi cho phép app lắng nghe cổng nội bộ. Hãy cho phép nếu bạn muốn nhận thông báo video mới theo thời gian gần thực.
- Không đóng hẳn app nếu muốn app tiếp tục theo dõi. Có thể thu nhỏ xuống system tray.

---

## 2. Khái niệm layout

MMO App dùng mô hình:

```text
Một layout = nhiều kênh YouTube nguồn → một kênh TikTok đích
```

Bạn có thể tạo nhiều layout để phục vụ nhiều kênh TikTok khác nhau.

Ví dụ:

```text
Layout 1: TikTok Review Phim
  - YouTube: https://www.youtube.com/@kenhphim1
  - YouTube: https://www.youtube.com/@kenhphim2
  → Upload vào TikTok: Review Phim

Layout 2: TikTok Công Nghệ
  - YouTube: https://www.youtube.com/@techchannel1
  - YouTube: https://www.youtube.com/@techchannel2
  - YouTube: https://www.youtube.com/@techchannel3
  → Upload vào TikTok: Cong Nghe 24h
```

Mỗi kênh YouTube chỉ nên nằm trong một layout. Nếu cùng một kênh YouTube bị nhập ở nhiều layout, app sẽ báo lỗi để tránh upload nhầm TikTok.

---

## 3. Cấu hình luồng YouTube → TikTok

Mở tab:

```text
Luồng YouTube → TikTok
```

### Thêm một layout mới

1. Bấm:

   ```text
   ＋ Add layout
   ```

2. Nhập tên layout.

   Ví dụ:

   ```text
   Review Phim
   ```

3. Nhập tên kênh TikTok.

   Ví dụ:

   ```text
   TikTok Review Phim
   ```

4. Nhập TikTok Chrome CDP URL nếu layout này dùng một Chrome profile riêng.

   Ví dụ:

   ```text
   http://127.0.0.1:9222
   ```

5. Ở phần `Nguồn YouTube`, nhập URL kênh YouTube.

   Ví dụ:

   ```text
   https://www.youtube.com/@tien5140
   ```

6. Nếu muốn thêm kênh YouTube thứ hai cho cùng kênh TikTok, bấm:

   ```text
   ＋ YouTube
   ```

7. Sau khi nhập xong, bấm lưu cấu hình.

App sẽ tự lấy `channel_id` từ URL kênh YouTube để dùng cho WebSub. Người dùng không cần tự tìm hoặc nhập `channel_id`.

### Các dạng URL YouTube được khuyến nghị

Bạn có thể nhập dạng handle:

```text
https://www.youtube.com/@tien5140
```

Hoặc dạng channel ID trực tiếp:

```text
https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx
```

Nếu URL không phải URL kênh, app sẽ báo lỗi.

---

## 4. Cấu hình WebSub và RSS fallback

Mở tab:

```text
Cấu hình
```

MMO App ưu tiên WebSub để nhận video mới. RSS chỉ dùng làm fallback.

### WebSub hoạt động như thế nào?

Khi kênh YouTube có video mới, YouTube gửi thông báo tới callback công khai của bạn. App nhận thông báo đó rồi đưa video vào hàng đợi tải/xử lý/upload.

Luồng đơn giản:

```text
YouTube WebSub Hub
  → HTTPS domain của bạn
  → Cloudflare Tunnel / reverse proxy
  → http://127.0.0.1:8000/webhook trên máy chạy MMO App
```

### Các trường cấu hình WebSub

#### WebSub bind host

Đây là địa chỉ nội bộ mà app lắng nghe trên máy tính.

Khuyến nghị:

```text
127.0.0.1
```

Dùng `127.0.0.1` khi bạn chạy Cloudflare Tunnel hoặc reverse proxy trên cùng máy.

Chỉ dùng `0.0.0.0` nếu bạn thật sự muốn app lắng nghe từ các thiết bị khác trong mạng LAN.

#### WebSub port

Cổng nội bộ của app.

Ví dụ:

```text
8000
```

Callback nội bộ sẽ là:

```text
http://127.0.0.1:8000/webhook
```

#### Public domain WebSub

Đây là URL HTTPS công khai trỏ về app thông qua tunnel/proxy.

Ví dụ:

```text
https://mmo-webhook.example.com
```

Khi đó callback công khai sẽ là:

```text
https://mmo-webhook.example.com/webhook
```

Lưu ý: trường này phải bắt đầu bằng `https://`.

#### RSS fallback interval

Khoảng thời gian app quét RSS khi cần fallback.

Ví dụ:

```text
900
```

Nghĩa là 900 giây, tương đương 15 phút.

### Ví dụ cấu hình Cloudflare Tunnel

Nếu MMO App đang lắng nghe:

```text
http://127.0.0.1:8000/webhook
```

Bạn cấu hình tunnel để domain:

```text
https://mmo-webhook.example.com
```

forward về:

```text
http://127.0.0.1:8000
```

Trong MMO App nhập:

```text
WebSub bind host: 127.0.0.1
WebSub port: 8000
Public domain WebSub: https://mmo-webhook.example.com
RSS fallback interval: 900
```

---

## 5. Cấu hình xử lý video

Mở tab:

```text
Cấu hình
```

### Tỷ lệ video đầu ra

MMO App hỗ trợ các tỷ lệ phổ biến:

```text
original
9:16
4:5
1:1
16:9
```

Gợi ý:

- TikTok/Reels/Shorts: chọn `9:16`.
- Feed dọc nhưng ít crop hơn: chọn `4:5`.
- Video vuông: chọn `1:1`.
- Giữ nguyên video gốc: chọn `original`.
- Video ngang: chọn `16:9`.

Khi chọn tỷ lệ cố định, app sẽ scale vừa khung và thêm nền đen ở phần thừa để giữ toàn bộ hình gốc, không crop và không kéo méo hình. Với video ngang chuyển sang `9:16`, phần nền đen phía trên/dưới rất phù hợp để chèn title và Part.

### Chèn title và Part lên video

Bật tùy chọn:

```text
Chèn title phía trên và Part phía dưới video
```

Khi bật, app sẽ:

- Chèn title YouTube lên phía trên video.
- Nếu video bị cắt thành nhiều phần, chèn `Part 1`, `Part 2`, ... ở phía dưới.
- Cho phép chọn font chữ, màu chữ và cỡ chữ.

Khi chia video theo thời lượng, nếu đoạn cuối ngắn hơn một nửa thời lượng yêu cầu thì app sẽ gộp đoạn cuối vào phần trước đó. Ví dụ đặt 60 giây/phần, video dài 130 giây sẽ ra 2 phần: 60 giây và 70 giây, không tạo thêm phần 10 giây.

Ví dụ cấu hình:

```text
Font chữ: C:\Windows\Fonts\arial.ttf
Màu chữ: white
Cỡ chữ: 56
```

Hoặc dùng mã màu:

```text
Màu chữ: #FFD700
```

Nếu để trống font chữ, app sẽ dùng Arial mặc định trên Windows.

---

## 6. Caption khi upload TikTok

Caption TikTok được lấy từ title YouTube, không lấy từ tên file.

Ví dụ video YouTube có title:

```text
10 Bí Mật Thú Vị Về Đại Dương
```

Nếu video không bị chia part, caption TikTok là:

```text
10 Bí Mật Thú Vị Về Đại Dương
```

Nếu video bị chia thành 3 part, caption lần lượt là:

```text
10 Bí Mật Thú Vị Về Đại Dương - Part 1
10 Bí Mật Thú Vị Về Đại Dương - Part 2
10 Bí Mật Thú Vị Về Đại Dương - Part 3
```

File xử lý có thể nằm dạng:

```text
processed\TikTok Review Phim\<YouTube video ID>\001.mp4
processed\TikTok Review Phim\<YouTube video ID>\002.mp4
```

Nhưng caption vẫn lấy từ database/hàng đợi upload, không lấy từ tên thư mục hoặc tên file.

---

## 7. Cấu hình upload TikTok

MMO App dùng Chrome đã đăng nhập TikTok để upload.

### Chrome CDP URL là gì?

Đây là địa chỉ app dùng để điều khiển Chrome profile đã đăng nhập TikTok.

Ví dụ:

```text
http://127.0.0.1:9222
```

Mỗi layout có thể có CDP URL riêng nếu mỗi kênh TikTok dùng một Chrome profile riêng.

### Chế độ upload TikTok

Trong mỗi layout có trường:

```text
Chế độ upload
```

Có 2 lựa chọn:

```text
cdp
gpm
```

- `cdp`: app kết nối tới Chrome đang mở cổng CDP rồi dùng Playwright để upload/đăng video.
- `gpm`: lưu cấu hình để dùng GPM Automation trong bước tích hợp tiếp theo.

Nếu chọn `cdp`, cần nhập:

```text
TikTok Chrome CDP URL
```

Ví dụ:

```text
http://127.0.0.1:9222
```

Nếu chọn `gpm`, cần nhập:

```text
GPM Profile ID
```

Lưu ý: ở bản hiện tại, chế độ `gpm` mới được lưu cấu hình và route tài khoản. Adapter upload thật qua GPM sẽ được tích hợp ở bản tiếp theo, nên job upload sẽ dừng ở trạng thái cần thao tác nếu chọn `gpm`.

### Giới hạn đăng/ngày

Trong mỗi layout có trường:

```text
Giới hạn đăng/ngày
```

Đây là giới hạn nội bộ của MMO App cho từng kênh TikTok, dùng để tránh đăng quá nhiều video trong một ngày.

Ví dụ:

```text
3  = tối đa 3 video/ngày
10 = tối đa 10 video/ngày
0  = không giới hạn
```

Số lượt đã đăng sẽ tự reset khi sang ngày mới.

### Cho phép app bấm Đăng

Trong tab `Cấu hình`, có tùy chọn:

```text
Cho phép click Đăng TikTok (tác vụ có tác động công khai)
```

Nếu chưa bật, app có thể chuẩn bị upload nhưng sẽ dừng trước bước đăng công khai.

Khuyến nghị khi chạy thử lần đầu:

1. Chưa bật tùy chọn đăng.
2. Cho app tải và xử lý video.
3. Kiểm tra caption/video.
4. Khi đã ổn, bật tùy chọn đăng.

---

## 8. Bắt đầu chạy

Sau khi cấu hình xong:

1. Bấm lưu cấu hình.
2. Kiểm tra tab dashboard/log nếu có.
3. Bấm:

   ```text
   Bắt đầu
   ```

Khi app chạy, trạng thái sẽ chuyển sang đang chạy. Nếu muốn tạm dừng, bấm:

```text
Tạm dừng
```

Khi vừa mở app, MMO App sẽ không tự chạy. Người dùng cần nhập/lưu cấu hình trước, sau đó bấm `Bắt đầu`.

---

## 9. Ví dụ cấu hình hoàn chỉnh

### Mục tiêu

Ba kênh YouTube cùng phục vụ một kênh TikTok tên `TikTok Kiến Thức`.

Một kênh YouTube khác phục vụ kênh TikTok tên `TikTok Giải Trí`.

### Layout 1

```text
Tên layout: Kien Thuc
Kênh TikTok: TikTok Kiến Thức
TikTok Chrome CDP URL: http://127.0.0.1:9222

Nguồn YouTube:
  https://www.youtube.com/@science1
  https://www.youtube.com/@history1
  https://www.youtube.com/@facts1
```

### Layout 2

```text
Tên layout: Giai Tri
Kênh TikTok: TikTok Giải Trí
TikTok Chrome CDP URL: http://127.0.0.1:9223

Nguồn YouTube:
  https://www.youtube.com/@funnyclips1
```

### WebSub

```text
WebSub bind host: 127.0.0.1
WebSub port: 8000
Public domain WebSub: https://mmo-webhook.example.com
RSS fallback interval: 900
```

### Xử lý video

```text
Tỷ lệ video đầu ra: 9:16
Chèn title phía trên và Part phía dưới video: bật
Font chữ: C:\Windows\Fonts\arial.ttf
Màu chữ: white
Cỡ chữ: 56
```

### Upload TikTok

```text
Cho phép click Đăng TikTok: tắt khi test, bật khi chạy thật
```

---

## 10. Kiểm tra khi app không chạy như mong đợi

### Bấm Bắt đầu nhưng không thấy phản hồi

Kiểm tra:

- Đã bấm lưu cấu hình chưa.
- Có ít nhất một layout chưa.
- Trong layout có tên kênh TikTok chưa.
- Trong layout có ít nhất một URL YouTube chưa.
- URL YouTube có đúng dạng kênh không.
- Nếu dùng WebSub, `Public domain WebSub` có bắt đầu bằng `https://` không.
- Cổng WebSub có bị app khác chiếm không.

### Không thấy video mới vào hàng đợi

Kiểm tra:

- WebSub chỉ nhận video mới sau khi subscribe thành công.
- Video đã đăng trước khi app subscribe có thể không tự vào hàng đợi qua WebSub.
- RSS fallback có thể cần chờ tới kỳ quét tiếp theo.
- Public domain/tunnel có forward đúng về `http://127.0.0.1:8000` không.

### Upload TikTok không chạy

Kiểm tra:

- Chrome profile đã đăng nhập TikTok chưa.
- CDP URL có đúng không.
- TikTok có đang yêu cầu xác minh/captcha không.
- Đã bật `Cho phép click Đăng TikTok` chưa.

### Chữ overlay không hiện đúng tiếng Việt

Kiểm tra:

- Font đang chọn có hỗ trợ tiếng Việt không.
- Thử dùng:

  ```text
  C:\Windows\Fonts\arial.ttf
  ```

---

## 11. Thư mục dữ liệu

App sử dụng chung cấu hình, database, hàng đợi, log và vòng đời chạy. Người dùng không cần mở các project cũ.

Video sau xử lý được gom theo kênh TikTok tương ứng.

Ví dụ:

```text
processed\TikTok Kiến Thức\<YouTube video ID>\001.mp4
processed\TikTok Kiến Thức\<YouTube video ID>\002.mp4

processed\TikTok Giải Trí\<YouTube video ID>\001.mp4
```

Điều này giúp kiểm tra nhanh video nào thuộc kênh TikTok nào.

---

## 12. Quy trình chạy thử khuyến nghị

Lần đầu dùng app, nên chạy theo thứ tự:

1. Tạo một layout với một kênh YouTube test.
2. Nhập một kênh TikTok test.
3. Cấu hình WebSub hoặc để RSS fallback chạy.
4. Chọn tỷ lệ video `9:16`.
5. Bật overlay title/part nếu muốn.
6. Chưa bật `Cho phép click Đăng TikTok`.
7. Bấm `Bắt đầu`.
8. Kiểm tra video đã tải/xử lý/caption.
9. Khi ổn, bật `Cho phép click Đăng TikTok`.
10. Chạy thật với nhiều layout và nhiều kênh YouTube.
