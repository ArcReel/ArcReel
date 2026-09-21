import type enAuth from "@/i18n/en/auth";

export default {
  'login': 'Đăng nhập',
  'logging_in': 'Đang đăng nhập...',
  'login_failed': 'Đăng nhập thất bại',
  'username': 'Tên đăng nhập',
  'auth_disabled_banner_title': 'Xác thực đang tắt.',
  'auth_disabled_banner_body': 'Mọi API quản trị đều truy cập được mà không cần đăng nhập. Chỉ dùng trên máy cục bộ được bảo vệ bởi ranh giới mạng riêng; khi triển khai từ xa hãy giữ xác thực bật.',
  'password': 'Mật khẩu',
} satisfies Record<keyof typeof enAuth, string>;
