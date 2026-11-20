# AutoGGForm — Công cụ Quét & Tự Động Điền Google Form

Ứng dụng web cục bộ giúp quét cấu trúc Google Form nhiều trang (tự chọn đáp án đầu để qua trang và dừng lại **không ấn nút Gửi**), cấu hình tỷ lệ `%` chọn đáp án (tối đa `100%`), chọn số lượng ngẫu nhiên trong khoảng cho câu nhiều lựa chọn (`Checkbox`), thiết lập giãn cách thời gian giữa mỗi lần điền và theo dõi tiến độ trực tiếp.

---

## Hướng dẫn cài đặt & chạy Web nhanh

### Yêu cầu môi trường
- **Python 3.10+** (đã test trên Python 3.15)
- **Playwright Chromium**

### Cách 1: Chạy nhanh trên Windows (Khuyên dùng)
1. Cài đặt thư viện (chỉ cần làm 1 lần đầu):
   ```powershell
   pip install -r requirements.txt
   playwright install chromium
   ```
2. Nhấp đúp vào file **`run.bat`** trong thư mục dự án (hoặc chạy `.\run.bat` trong terminal).
3. Trình duyệt sẽ tự mở tại địa chỉ: **`http://127.0.0.1:8080`**

### Cách 2: Chạy bằng lệnh Terminal
```powershell
pip install -r requirements.txt
playwright install chromium
python app.py
```
Sau khi thấy dòng `AutoGGForm server listening at http://127.0.0.1:8080`, mở trình duyệt và truy cập **`http://127.0.0.1:8080`**.

> **Lưu ý:** Có thể đổi cổng mặc định qua biến môi trường `PORT`, ví dụ:
> `$env:PORT=8088; python app.py`

---

## Hướng dẫn sử dụng trên giao diện Web

1. **Bước 1 — Quét câu hỏi (Không ấn gửi):**
   - Dán đường dẫn Google Form (`https://docs.google.com/forms/d/e/.../viewform`) và bấm **Quét câu hỏi** (hoặc bấm **Tải mẫu thử nghiệm** để xem trước giao diện).
   - Hệ thống mở trình duyệt Chromium, tự chọn đáp án đầu tiên trên từng trang để vượt qua các câu bắt buộc, chuyển hết các trang bằng nút **Tiếp** và **tự dừng khi gặp nút Gửi cuối cùng**.
2. **Bước 2 — Cấu hình tỷ lệ % & số lượng ngẫu nhiên:**
   - Chỉnh thanh trượt hoặc ô nhập `%` cho từng đáp án (tổng mỗi câu được khóa tối đa `100%`, có nút **Chia đều 100%**).
   - Với câu nhiều lựa chọn (`Checkbox` / `Lưới hộp kiểm`), bạn có thể nhập:
     - **`% Chọn câu hỏi này`** (`0%` – `100%`).
     - **`Số lượng ngẫu nhiên từ [Min] đến [Max]`** đáp án trong mỗi mẫu.
     - **`%` ưu tiên từng đáp án** (tối đa `100%`).
3. **Bước 3 — Chạy tự động & Theo dõi:**
   - Điền **Số mẫu khảo sát mong muốn** (`1` – `10.000`).
   - Điền **Giãn cách điền mỗi form** (`Từ [n] phút tới [n] phút`, nhập `0` tới `0` nếu muốn chạy liên tục không nghỉ).
   - Bấm **Bắt đầu điền** và theo dõi KPI tiến độ, đồng hồ đếm ngược giãn cách và bảng nhật ký chi tiết. Có thể bấm **Tạm dừng / Tiếp tục** hoặc **Dừng hẳn** bất kỳ lúc nào.

---

## Cấu trúc thư mục dự án

```text
D:\Auto_GGForm\
├── app.py                  # Server HTTP, engine phân phối tỷ lệ % & Playwright runner
├── run.bat                 # Script khởi chạy nhanh server + mở trình duyệt (Windows)
├── requirements.txt        # Danh sách thư viện Python cần thiết (playwright)
├── README.md               # Tài liệu hướng dẫn cài đặt, chạy và sử dụng
├── static/                 # Mã nguồn giao diện Web (nền trắng - Light Theme)
│   ├── index.html          # Giao diện chính (gồm bảng hướng dẫn ở đầu trang)
│   ├── styles.css          # CSS 3-layer design tokens
│   └── app.js              # Logic cấu hình %, validate và polling trạng thái
├── tests/                  # Kiểm thử tự động
│   └── test_app.py         # Kiểm thử phân phối %, quét/điền Playwright & API/CSS tokens
├── docs/                   # Tài liệu tham chiếu thiết kế
│   └── design.md           # Tài liệu đặc tả giao diện gốc
└── skills/                 # Thư mục các skill hỗ trợ phát triển trong workspace
    ├── design-system/
    ├── ponytail/
    └── ui-ux-pro-max/
```

---

## Kiểm thử tự động

Chạy lệnh sau tại thư mục gốc `D:\Auto_GGForm`:

```powershell
python tests/test_app.py
```

Bộ kiểm thử xác minh:
- Thuật toán chia tỷ lệ `allocate_quotas`, `allocate_boolean_quota`, `sample_checkbox_options`, `build_submission_plan`.
- Luồng Playwright quét form 2 trang đảm bảo **không ấn gửi** (`submitted_count == 0`) và điền gửi hoàn chỉnh (`submitted_count == 1`).
- Xác thực HTTP API và kiểm tra CSS không chứa mã hex thô ngoài `:root`.

---

## Giới hạn cần lưu ý
- Google Form yêu cầu đăng nhập tài khoản Google, bật CAPTCHA, hoặc có logic rẽ nhánh section phức tạp ngoài nhánh đáp án đầu tiên sẽ không tự quét/gửi hoàn toàn được.
