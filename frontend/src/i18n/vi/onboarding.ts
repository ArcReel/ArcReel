import type enOnboarding from '@/i18n/en/onboarding';

export default {
  // Các bước hướng dẫn
  'welcome_title': 'Chào mừng đến với [[brand]]',
  'welcome_body': 'Đưa vào một cuốn tiểu thuyết, hệ thống sẽ tách thành phân cảnh, tạo hình ảnh và dựng thành video ngắn. Phần hướng dẫn này chỉ giới thiệu giao diện, không thay đổi bất kỳ dữ liệu nào của bạn.',
  'lobby_create_title': 'Tạo dự án từ đây',
  'lobby_create_body': 'Mỗi dự án đều bắt đầu từ một cuốn tiểu thuyết. Hãy nhập tệp .txt, .docx, .epub hoặc .pdf, [[brand]] sẽ đọc và chia thành từng tập để bạn triển khai.',
  'lobby_demo_title': 'Dự án sẽ trông như thế này',
  'lobby_demo_body': 'Đây là thẻ ví dụ, không phải dự án của bạn. Nhãn hiển thị giai đoạn hiện tại, các số liệu bên dưới theo dõi tiến độ của nhân vật, bối cảnh, đạo cụ và các tập.',
  'lobby_settings_title': 'Cấu hình nhà cung cấp nằm trong Cài đặt',
  'lobby_settings_body': 'Tạo hình ảnh, video và văn bản đều chạy trên nhà cung cấp do bạn chọn. Chấm đỏ trên nút này nghĩa là còn mục bắt buộc chưa được cấu hình — mở Cài đặt sẽ thấy ngay phần còn thiếu.',
  'settings_providers_title': 'Cấu hình một nhà cung cấp',
  'settings_providers_body': 'Cấu hình ít nhất một nhà cung cấp ở đây — nhập API Key và chạy thử kết nối trước khi bắt đầu tạo nội dung.',
  'settings_agent_title': 'Kết nối Agent',
  'settings_agent_body': 'Tạo tổng quan, kịch bản và trả lời trò chuyện đều cần thông tin xác thực Anthropic — hãy thêm vào đây.',
  'finish_title': 'Đến lượt bạn',
  'finish_body': 'Hãy bắt đầu bằng cách nhập một cuốn tiểu thuyết, phần còn lại làm từng bước một. Muốn xem lại, hãy mở Cài đặt → Giới thiệu.',

  // Điều khiển hướng dẫn
  'next': 'Tiếp tục',
  'prev': 'Quay lại',
  'done': 'Hoàn tất',
  'skip': 'Bỏ qua',
  'close': 'Đóng hướng dẫn',
  'progress': 'Bước {{current}} / {{total}}',

  // Thẻ minh hoạ hiển thị trong lúc hướng dẫn
  'demo_section_eyebrow': 'Dự án mẫu',
  'demo_section_note': 'Chỉ hiển thị trong lúc hướng dẫn',
  'demo_project_title': 'Alice ở xứ sở thần tiên',
  'demo_project_style': 'Truyện tranh màu nước',

  // Mục trong Cài đặt → Giới thiệu
  'replay_title': 'Hướng dẫn sử dụng',
  'replay_desc': 'Xem lại phần hướng dẫn lần đầu. Chỉ giới thiệu giao diện, không thay đổi dữ liệu.',
  'replay_action': 'Xem lại hướng dẫn',
} satisfies Record<keyof typeof enOnboarding, string>;
