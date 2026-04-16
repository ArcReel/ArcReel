# Hướng Dẫn Xuất Bản Nháp JianYing (CapCut)

Xuất các đoạn video đã tạo trong ArcReel theo tập thành file bản nháp JianYing (剪映) / CapCut, mở trực tiếp trong phiên bản desktop để chỉnh sửa — điều chỉnh nhịp, thêm phụ đề, chuyển cảnh, lồng tiếng v.v.

## Điều Kiện Tiên Quyết

- Đã hoàn thành tạo video ít nhất một tập trong ArcReel
- Đã cài **JianYing / CapCut Desktop** (phiên bản 5.x hoặc 6+)

## Các Bước Thực Hiện

### 1. Tìm thư mục bản nháp JianYing

Trước khi xuất, bạn cần biết đường dẫn lưu bản nháp JianYing trên máy.

**macOS:**
```
/Users/<tên-người-dùng>/Movies/JianyingPro/User Data/Projects/com.lveditor.draft
```

**Windows:**
```
C:\Users\<tên-người-dùng>\AppData\Local\JianyingPro\User Data\Projects\com.lveditor.draft
```

> **Mẹo**: Có thể xem "Đường dẫn bản nháp" trong cài đặt JianYing. Nếu bạn đã thay đổi đường dẫn mặc định, hãy dùng thư mục thực tế.

### 2. Thực hiện xuất trong ArcReel

1. Mở dự án mục tiêu
2. Nhấn nút **Xuất** ở góc trên phải
3. Chọn **Xuất bản nháp JianYing**

### 3. Điền thông số xuất

| Thông số | Mô tả |
|----------|-------|
| **Tập** | Chọn tập cần xuất (dự án nhiều tập sẽ hiển thị bộ chọn) |
| **Phiên bản JianYing** | Chọn **6.0+** (khuyến nghị) hoặc **5.x**, cần khớp với phiên bản đã cài |
| **Thư mục bản nháp** | Nhập đường dẫn bản nháp đã tìm ở trên (lần đầu nhập sẽ tự ghi nhớ) |

Nhấn **Xuất bản nháp**, trình duyệt sẽ tải về file ZIP.

### 4. Giải nén vào thư mục bản nháp

Giải nén file ZIP đã tải vào thư mục bản nháp JianYing. Cấu trúc sau khi giải nén:

```
com.lveditor.draft/
├── ... (các bản nháp đã có)
└── {tên-dự-án}_Tập{N}/           ← Thư mục giải nén
    ├── draft_info.json            (JianYing 6+) hoặc draft_content.json (5.x)
    ├── draft_meta_info.json
    └── assets/
        ├── segment_S1.mp4
        ├── segment_S2.mp4
        └── ...
```

### 5. Mở trong JianYing

1. Mở (hoặc khởi động lại) JianYing Desktop
2. Tìm bản nháp **{tên-dự-án}\_Tập{N}** mới xuất hiện trong danh sách "Bản nháp"
3. Nhấp đúp để mở và xem tất cả đoạn video trên timeline

## Nội Dung Xuất

### Chế độ Thuyết minh (Narration)

- **Track video**: Tất cả đoạn video đã tạo xếp theo thứ tự
- **Track phụ đề**: Tự động kèm theo văn bản tiểu thuyết gốc tương ứng mỗi đoạn (chữ trắng, viền đen), có thể tùy chỉnh kiểu và vị trí trong JianYing

### Chế độ Phim (Drama)

- **Track video**: Xếp theo thứ tự cảnh tất cả đoạn video đã tạo
- Không kèm phụ đề (cấu trúc phụ đề hội thoại nhiều nhân vật phức tạp, khuyến nghị thêm thủ công trong JianYing)

### Kích Thước Canvas

Tự động xác định theo cài đặt dự án:
- Dọc (9:16) → 1080×1920
- Ngang (16:9) → 1920×1080

Nếu dự án chưa đặt tỷ lệ, sẽ tự phát hiện từ file video đầu tiên.

## Câu Hỏi Thường Gặp

### Không thấy bản nháp đã xuất trong JianYing?

- Xác nhận ZIP đã giải nén đúng thư mục bản nháp
- Xác nhận thư mục giải nén nằm trực tiếp trong thư mục bản nháp (không lồng thêm thư mục)
- Thử khởi động lại JianYing

### Sai phiên bản?

Phiên bản JianYing chọn khi xuất phải khớp với phiên bản đã cài:
- JianYing 6.0 trở lên → Chọn **6.0+**
- JianYing 5.x → Chọn **5.x**

Nếu chọn sai, xuất lại và chọn đúng phiên bản.

### Thiếu một số đoạn video?

Bản xuất chỉ bao gồm các đoạn video đã tạo thành công. Nếu một số đoạn chưa tạo hoặc tạo thất bại, chúng sẽ không xuất hiện trong bản nháp. Quay lại ArcReel tạo bổ sung rồi xuất lại.
