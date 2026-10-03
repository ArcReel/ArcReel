import type enAuth from "@/i18n/en/auth";

export default {
  'login': 'Đăng nhập',
  'logging_in': 'Đang đăng nhập...',
  'login_failed': 'Đăng nhập thất bại',
  'username': 'Tên đăng nhập',
  'password': 'Mật khẩu',
  'login_subtitle': 'Đăng nhập bằng tài khoản của bạn',
} satisfies Record<keyof typeof enAuth, string>;
