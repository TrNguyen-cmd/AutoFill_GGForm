# AutoGGForm — Công cụ Quét & Tự Động Điền Google Form

Ứng dụng web cục bộ giúp quét cấu trúc Google Form nhiều trang (tự chọn đáp án đầu để qua trang và dừng lại **không ấn nút Gửi**), cấu hình tỷ lệ `%` chọn đáp án, hỗ trợ tỷ lệ độc lập `0%–100%` từng lựa chọn và khoảng số lượng ngẫu nhiên cho câu nhiều lựa chọn (`Checkbox`), thiết lập giãn cách thời gian giữa mỗi lần điền và theo dõi tiến độ trực tiếp.

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
   - Dán đường dẫn Google Form (`https://docs.google.com/forms/d/e/.../viewform` hoặc link rút gọn `https://forms.gle/...`) và bấm **Quét câu hỏi** (hoặc bấm **Tải mẫu thử nghiệm** để xem trước giao diện).
   - Hệ thống mở trình duyệt Chromium, tự chọn đáp án đầu tiên trên từng trang để vượt qua các câu bắt buộc, tự nhận diện các giới hạn số lượng chọn của câu Checkbox (ví dụ: *chọn tối đa 2 hoặc 3 đáp án*), chuyển hết các trang bằng nút **Tiếp** và **tự dừng khi gặp nút Gửi cuối cùng**.
2. **Bước 2 — Cấu hình tỷ lệ % & số lượng ngẫu nhiên:**
   - **Câu 1 lựa chọn (`Radio`, `Dropdown`, `Scale`, `Grid Radio`):** Tổng tỷ lệ `%` giữa các đáp án được khóa tối đa `100%`, kèm nút **Chia đều 100%** và **Đặt 0%**.
   - **Câu nhiều lựa chọn (`Checkbox`, `Grid Checkbox`):**
     - **`% Chọn câu hỏi này`** (`0%` – `100%`): Tỷ lệ số người sẽ trả lời câu hỏi này.
     - **`Số lượng ngẫu nhiên từ [Min] đến [Max]`**: Tự động nhận diện theo ràng buộc trên Google Form (`_detect_checkbox_bounds`) và cho phép chỉnh sửa.
     - **`%` từng đáp án (`0%` – `100%`)**: Cho phép đặt tỷ lệ số người chọn độc lập cho từng đáp án (tổng các đáp án không bị giới hạn ở `100%`), kèm nút **Đặt tất cả 50%**, **Đặt tất cả 100%** và **Đặt 0%**.
3. **Bước 3 — Chạy tự động & Theo dõi:**
   - Điền **Số mẫu khảo sát mong muốn** (`1` – `10.000`).
   - Điền **Giãn cách điền mỗi form** (`Từ [n] phút tới [n] phút`, nhập `0` tới `0` nếu muốn chạy liên tục không nghỉ).
   - Bấm **Bắt đầu điền** và theo dõi KPI tiến độ, đồng hồ đếm ngược giãn cách và bảng nhật ký chi tiết từng mẫu. Có thể bấm **Tạm dừng / Tiếp tục** hoặc **Dừng hẳn** bất kỳ lúc nào.

---

## Tính năng kỹ thuật chính

- **Phân phối không thiên lệch mẫu nhỏ:** Sử dụng `allocate_quotas`, `allocate_boolean_quota` và `plan_checkbox_schedule` phân bổ chính xác theo số lượng mẫu và lấy mẫu ngẫu nhiên phần dư có trọng số khi chạy ít mẫu (`total_samples = 1`).
- **Xử lý rẽ nhánh section & "Mục khác:":** Ghép câu hỏi theo tiêu đề xuyên các trang khi có câu hỏi điều kiện chuyển section (các câu ở trang bị bỏ qua tự động ghi nhận trống `""`), đồng thời tự điền `"Khác"` khi chọn tùy chọn `Mục khác:` (`__other_option__`).
- **Kiểm soát chuyển trang & xác nhận gửi:** Chờ DOM/điều hướng hoàn tất khi bấm **Tiếp** (`_advance_to_next_page`) và kiểm tra trang xác nhận của Google Form sau khi bấm **Gửi**.

---

## Cấu trúc thư mục dự án

```text
D:\Auto_GGForm\
├── .gitignore              # Bỏ qua docs/, skills/, __pycache__/, file log và IDE config
├── app.py                  # Server HTTP, engine phân phối tỷ lệ % & Playwright runner
├── run.bat                 # Script khởi chạy nhanh server + mở trình duyệt (Windows)
├── requirements.txt        # Danh sách thư viện Python cần thiết (playwright)
├── README.md               # Tài liệu hướng dẫn cài đặt, chạy và sử dụng
├── static/                 # Mã nguồn giao diện Web (nền trắng - Light Theme)
│   ├── index.html          # Giao diện chính
│   ├── styles.css          # CSS 3-layer design tokens
│   └── app.js              # Logic cấu hình %, validate và polling trạng thái
└── tests/                  # Kiểm thử tự động
    └── test_app.py         # Kiểm thử phân phối %, quét/điền Playwright & API/CSS tokens
```

---

## Kiểm thử tự động

Chạy lệnh sau tại thư mục gốc `D:\Auto_GGForm`:

```powershell
python tests/test_app.py
```

Bộ kiểm thử xác minh:
- Thuật toán chia tỷ lệ `allocate_quotas`, `allocate_boolean_quota`, `sample_checkbox_options`, `plan_checkbox_schedule` (bao gồm tổng `%` checkbox > `100%`) và `build_submission_plan`.
- Luồng Playwright quét form 2 trang (có cấu trúc `<div role="list"><div role="listitem">` lồng nhau) đảm bảo **không tách đáp án**, **không ấn gửi** (`submitted_count == 0`) và điền gửi hoàn chỉnh (`submitted_count == 1`).
- Xác thực HTTP API và kiểm tra CSS không chứa mã hex thô ngoài `:root`.

---

## Giới hạn cần lưu ý
- Google Form yêu cầu đăng nhập tài khoản Google hoặc bật CAPTCHA sẽ không tự quét/gửi được.
- Với form có rẽ nhánh section phức tạp, trình quét chỉ duyệt theo nhánh của đáp án đầu tiên; các tùy chọn `Mục khác:` hiện mặc định điền giá trị `"Khác"`.
