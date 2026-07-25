import type enOnboarding from '@/i18n/en/onboarding';

export default {
  // Các bước hướng dẫn
  'welcome_title': 'Chào mừng đến với [[brand]]',
  'welcome_body': 'Đưa vào một cuốn tiểu thuyết, hệ thống sẽ tách thành phân cảnh, tạo hình ảnh và dựng thành video ngắn. Phần hướng dẫn này chỉ giới thiệu giao diện, không thay đổi bất kỳ dữ liệu nào của bạn.',
  'finish_title': 'Đến lượt bạn',
  'finish_body': 'Hãy bắt đầu bằng cách nhập một cuốn tiểu thuyết, phần còn lại làm từng bước một. Muốn xem lại, hãy mở Cài đặt → Giới thiệu.',

  // Điều khiển hướng dẫn
  'next': 'Tiếp tục',
  'prev': 'Quay lại',
  'done': 'Hoàn tất',
  'skip': 'Bỏ qua',
  'close': 'Đóng hướng dẫn',
  'progress': 'Bước {{current}} / {{total}}',

  // Mục trong Cài đặt → Giới thiệu
  'replay_title': 'Hướng dẫn sử dụng',
  'replay_desc': 'Xem lại phần hướng dẫn lần đầu. Chỉ giới thiệu giao diện, không thay đổi dữ liệu.',
  'replay_action': 'Xem lại hướng dẫn',
} satisfies Record<keyof typeof enOnboarding, string>;
