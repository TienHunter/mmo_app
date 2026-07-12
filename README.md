# MMO App

Ứng dụng desktop thống nhất cho flow YouTube → xử lý video → TikTok. Chạy dev:

```powershell
python -m pip install -r requirements.txt
python main.py
```

Build Windows bằng `build_windows.ps1`. Chỉ phát hành `dist\MMOApp-Windows-x64.zip`; bên trong có một entry point `MMOApp.exe`. Không phát hành các EXE của project cũ.

Mặc định ứng dụng chỉ chuẩn bị hàng upload. Người dùng phải cấu hình Chrome CDP đã đăng nhập TikTok và bật rõ tùy chọn cho phép đăng trước khi app click Post.

Video Processor hỗ trợ tỷ lệ đầu ra `original`, `9:16`, `4:5`, `1:1` và `16:9`. Các tỷ lệ cố định dùng scale + pad nền đen để giữ toàn bộ hình gốc, không crop và không kéo méo video.

## WebSub

Chế độ mặc định là `both`: WebSub nhận notification realtime và RSS chỉ là fallback.
Cloudflare Tunnel/domain HTTPS phải chuyển tiếp tới `http://127.0.0.1:8000`; callback công khai là `https://domain-cua-ban/webhook`. Host, port, public domain và RSS fallback interval đều chỉnh được trong tab Cấu hình.
