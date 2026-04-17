# Hướng Dẫn Bắt Đầu

Hướng dẫn này sẽ giúp bạn bắt đầu từ con số 0, sử dụng ArcReel để chuyển đổi tiểu thuyết thành video ngắn.

## Bạn Sẽ Học Được

1. **Chuẩn bị môi trường** — Lấy API key
2. **Triển khai dịch vụ** — Triển khai qua Docker
3. **Quy trình đầy đủ** — Từng bước từ tiểu thuyết đến video
4. **Mẹo nâng cao** — Tạo lại, kiểm soát chi phí, phát triển local

## Thời Gian Dự Kiến

- Chuẩn bị môi trường: 10-20 phút (chỉ lần đầu)
- Tạo một video 1 phút: khoảng 30 phút

## Ước Tính Chi Phí

ArcReel hỗ trợ nhiều nhà cung cấp (Gemini, Volcengine Ark, Grok, OpenAI và nhà cung cấp tùy chỉnh), ví dụ với Gemini:

| Loại | Mô hình | Đơn giá | Ghi chú |
|------|---------|---------|---------|
| Tạo hình ảnh | Nano Banana Pro | $0.134/ảnh (1K/2K) | Chất lượng cao, phù hợp thiết kế nhân vật |
| Tạo hình ảnh | Nano Banana 2 | $0.067/ảnh (1K) | Nhanh hơn, rẻ hơn, phù hợp phân cảnh |
| Tạo video | Veo 3.1 | $0.40/giây (1080p có âm thanh) | Chất lượng cao |
| Tạo video | Veo 3.1 Fast | $0.15/giây (1080p có âm thanh) | Nhanh hơn, rẻ hơn |
| Tạo video | Veo 3.1 Lite | Thấp hơn | Mô hình nhẹ, chỉ trên AI Studio |

> 💡 **Ví dụ** (Gemini): Một video ngắn gồm 10 cảnh (mỗi cảnh 8 giây)
> - Hình ảnh: 3 thiết kế nhân vật (Pro) + 10 phân cảnh (Flash) = $0.40 + $0.67 = $1.07
> - Video: 80 giây × $0.15 (chế độ Fast) = $12
> - **Tổng khoảng $13**

> 🎁 **Ưu đãi người mới**: Google Cloud tặng **$300 miễn phí** cho người dùng mới, có hiệu lực 90 ngày, đủ để tạo rất nhiều video!
>
> Chi phí các nhà cung cấp khác vui lòng tham khảo trang định giá chính thức, ArcReel cung cấp theo dõi chi phí real-time trong trang cài đặt.

---

## Chương 1: Chuẩn Bị Môi Trường

### 1.1 Lấy API Key Nhà Cung Cấp Hình Ảnh/Video

ArcReel hỗ trợ nhiều nhà cung cấp, **cấu hình ít nhất một** để bắt đầu:

| Nhà cung cấp | Địa chỉ lấy key | Ghi chú |
|--------------|------------------|---------|
| **Gemini** (Google) | [AI Studio](https://aistudio.google.com/apikey) | Cần gói trả phí, người mới tự động được $300 |
| **Volcengine Ark** | [Volcengine Console](https://console.volcengine.com/ark) | Tính phí theo token/ảnh (CNY) |
| **Grok** (xAI) | [xAI Console](https://console.x.ai/) | Tính phí theo ảnh/giây (USD) |
| **OpenAI** | [OpenAI Platform](https://platform.openai.com/) | Tính phí theo ảnh/giây (USD) |

Bạn cũng có thể thêm **nhà cung cấp tùy chỉnh** (bất kỳ API tương thích OpenAI / Google) qua trang cài đặt sau khi triển khai.

> ⚠️ API key là thông tin nhạy cảm, hãy bảo quản cẩn thận, không chia sẻ hoặc tải lên repository công khai.

### 1.2 Lấy API Key Anthropic

ArcReel tích hợp trợ lý AI dựa trên Claude Agent SDK, phụ trách sáng tạo kịch bản, hướng dẫn hội thoại thông minh và các khâu then chốt.

**Cách A: Sử dụng API chính thức Anthropic**

1. Truy cập [Anthropic Console](https://console.anthropic.com/)
2. Đăng ký tài khoản và tạo API key
3. Cấu hình sau trong trang Web UI

**Cách B: Sử dụng API bên thứ ba tương thích Anthropic**

Nếu không thể truy cập trực tiếp Anthropic API, cấu hình trong trang cài đặt:

- **Base URL** — Nhập địa chỉ dịch vụ trung chuyển hoặc API tương thích
- **Model** — Chỉ định tên mô hình (VD: `claude-sonnet-4-6`)
- Có thể cấu hình riêng mô hình mặc định cho Haiku / Sonnet / Opus và mô hình Subagent

### 1.3 Chuẩn Bị Máy Chủ

**Yêu cầu máy chủ:**

- Hệ điều hành: Linux / macOS / Windows WSL
- RAM: khuyến nghị 2GB+
- Đã cài Docker và Docker Compose

**Cài Docker (nếu chưa có):**

```bash
# Ubuntu / Debian
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# Đăng nhập lại rồi kiểm tra
docker --version
docker compose version
```

---

## Chương 2: Triển Khai Dịch Vụ

### 2.1 Tải và Khởi Động

#### Cách A: Triển khai mặc định (SQLite, khuyến nghị cho người mới)

```bash
# 1. Clone dự án
git clone https://github.com/ArcReel/ArcReel.git
cd ArcReel/deploy

# 2. Tạo file biến môi trường
cp .env.example .env

# 3. Khởi động dịch vụ
docker compose up -d
```

#### Cách B: Triển khai production (PostgreSQL, khuyến nghị cho sử dụng chính thức)

```bash
cd ArcReel/deploy/production

# Tạo file biến môi trường (cần đặt POSTGRES_PASSWORD)
cp .env.example .env

docker compose up -d
```

#### Cách C: Cài đặt 1-Click (macOS/Linux/Windows)

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/ArcReel/ArcReel/main/install.sh | bash

# Windows (PowerShell)
# Tải install.bat hoặc install.ps1 và chạy
```

Đợi container khởi động xong, truy cập trình duyệt tại **http://IP-máy-chủ:1241**

### 2.2 Cấu Hình Lần Đầu

1. Đăng nhập bằng tài khoản mặc định (tên đăng nhập `admin`, mật khẩu đặt trong `.env` qua `AUTH_PASSWORD`; nếu chưa đặt thì lần đầu khởi động sẽ tự tạo và ghi lại vào `.env`)
2. Vào **trang Cài đặt** (`/settings`)
3. Cấu hình **Anthropic API Key** (điều khiển trợ lý AI), hỗ trợ tùy chỉnh Base URL và mô hình
4. Cấu hình ít nhất một **API Key nhà cung cấp** hình ảnh/video (Gemini / Volcengine Ark / Grok / OpenAI), hoặc thêm nhà cung cấp tùy chỉnh
5. Điều chỉnh lựa chọn mô hình, giới hạn tốc độ và các tham số khác theo nhu cầu

> 💡 Tất cả cấu hình đều có thể thay đổi trong trang cài đặt, không cần sửa file cấu hình thủ công.

---

## Chương 3: Quy Trình Đầy Đủ

Các bước sau thực hiện trong giao diện Web UI.

### 3.1 Tạo Dự Án

1. Nhấn "Dự án mới" trong danh sách dự án
2. Nhập tên dự án (VD: "Tiểu thuyết của tôi")
3. Tải lên file văn bản tiểu thuyết (định dạng .txt)

### 3.2 Tạo Kịch Bản Phân Cảnh

Mở bảng trợ lý AI ở bên phải giao diện làm việc, trò chuyện để trợ lý tạo kịch bản:

- AI sẽ tự động phân tích nội dung tiểu thuyết, chia thành các đoạn phù hợp cho video
- Mỗi đoạn bao gồm mô tả hình ảnh, nhân vật xuất hiện, đạo cụ/bối cảnh quan trọng (manh mối)

**Điểm kiểm tra**: Kiểm tra cấu trúc kịch bản có hợp lý không, nhân vật và manh mối có đúng không.

### 3.3 Tạo Thiết Kế Nhân Vật

AI sẽ tạo thiết kế cho mỗi nhân vật, dùng để giữ nhất quán ngoại hình trong tất cả các cảnh sau.

**Điểm kiểm tra**: Kiểm tra hình ảnh nhân vật có phù hợp mô tả tiểu thuyết không, không hài lòng có thể tạo lại.

### 3.4 Tạo Thiết Kế Manh Mối

AI tạo hình ảnh tham chiếu cho đạo cụ và yếu tố bối cảnh quan trọng (như tín vật, địa điểm đặc biệt).

**Điểm kiểm tra**: Kiểm tra thiết kế manh mối có đúng kỳ vọng không.

### 3.5 Tạo Phân Cảnh

AI tạo hình ảnh tĩnh cho mỗi cảnh dựa trên kịch bản, tự động tham chiếu thiết kế nhân vật và manh mối để đảm bảo nhất quán.

**Điểm kiểm tra**: Kiểm tra bố cục cảnh, nhất quán nhân vật, không khí đúng hay chưa.

### 3.6 Tạo Video

Phân cảnh làm khung khởi đầu, thông qua nhà cung cấp video đã chọn (Veo 3.1 / Seedance / Grok / Sora 2 v.v.) tạo video động 4-8 giây.

Tác vụ tạo vào hàng đợi bất đồng bộ, bạn có thể theo dõi tiến trình real-time trong bảng giám sát. Kênh Image và Video hoạt động đồng thời độc lập, giới hạn RPM đảm bảo không vượt quota API.

**Điểm kiểm tra**: Xem trước từng video, không hài lòng có thể tạo lại riêng.

### 3.7 Ghép Video Cuối Cùng

Tất cả đoạn video được ghép bằng FFmpeg, thêm hiệu ứng chuyển cảnh và nhạc nền, xuất video hoàn chỉnh.

Mặc định xuất **9:16 dọc**, phù hợp đăng trên nền tảng video ngắn.

---

## Chương 4: Mẹo Nâng Cao

### 4.1 Lịch Sử Phiên Bản & Khôi Phục

Mỗi lần tạo lại tài nguyên, hệ thống tự động lưu lịch sử phiên bản. Trong giao diện dòng thời gian, bạn có thể duyệt phiên bản cũ và khôi phục bằng một click.

### 4.2 Kiểm Soát Chi Phí

**Xem thống kê chi phí:**

Trong trang cài đặt có thể xem số lần gọi API và chi tiết chi phí.

**Mẹo tiết kiệm:**

- Kiểm tra kỹ kết quả mỗi giai đoạn, giảm làm lại
- Tạo thử vài cảnh trước, hài lòng rồi mới tạo hàng loạt
- Tạo video dùng chế độ Fast tiết kiệm khoảng 60% chi phí
- Phân cảnh dùng mô hình Flash, thiết kế nhân vật dùng mô hình Pro

### 4.3 Nhập/Xuất Dự Án

Dự án hỗ trợ đóng gói lưu trữ, tiện sao lưu và di chuyển:

- **Xuất**: Đóng gói toàn bộ dự án (gồm tất cả tài nguyên) thành file lưu trữ
- **Nhập**: Khôi phục dự án từ file lưu trữ

---

## Chương 5: Câu Hỏi Thường Gặp

### H: Docker khởi động thất bại?

1. Xác nhận Docker đang chạy: `systemctl status docker`
2. Kiểm tra port 1241 có bị chiếm không: `ss -tlnp | grep 1241`
3. Xem log container: `docker compose logs` (chạy trong thư mục `deploy/` hoặc `deploy/production/`)

### H: Gọi API thất bại?

1. Xác nhận API Key nhà cung cấp trong trang cài đặt đã nhập đúng
2. Người dùng Gemini cần xác nhận đã bật gói trả phí (gói miễn phí không hỗ trợ tạo hình ảnh/video)
3. Kiểm tra mạng máy chủ có thể truy cập dịch vụ API của nhà cung cấp
4. Kiểm tra lượng sử dụng API trong console nhà cung cấp có vượt giới hạn không

### H: Nhân vật khác nhau giữa các cảnh?

1. Đảm bảo đã tạo thiết kế nhân vật trước
2. Kiểm tra chất lượng thiết kế nhân vật, không hài lòng thì tạo lại trước
3. Hệ thống sẽ tự động dùng thiết kế nhân vật làm tham chiếu, đảm bảo nhất quán giữa các cảnh

### H: Tạo video rất chậm?

Tạo video thường mất 1-3 phút/đoạn, đây là bình thường. Các yếu tố ảnh hưởng:

- Thời lượng video (4 giây vs 8 giây)
- Tải máy chủ API
- Tình trạng mạng

Hàng đợi hỗ trợ xử lý đồng thời, nhiều đoạn video có thể tạo cùng lúc.

### H: Bị gián đoạn khi đang tạo?

Hàng đợi hỗ trợ tiếp tục từ điểm dừng. Khi kích hoạt tạo lại, hệ thống tự động bỏ qua các đoạn đã hoàn thành, chỉ xử lý phần còn lại.

---

## Bước Tiếp Theo

Chúc mừng bạn đã hoàn thành hướng dẫn bắt đầu! Tiếp theo bạn có thể:

- 💰 Xem [chi tiết phí Google GenAI](google-genai-docs/Google视频&图片生成费用参考.md) và [chi tiết phí Volcengine Ark](ark-docs/火山方舟费用参考.md)
- 🐛 Gặp vấn đề? Gửi [Issue](https://github.com/ArcReel/ArcReel/issues) phản hồi
- 💬 Tham gia cộng đồng hỗ trợ

Nếu thấy dự án hữu ích, hãy cho một ⭐ Star ủng hộ nhé!
