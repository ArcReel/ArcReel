import type enAuth from "@/i18n/en/auth";

export default {
  'login': 'Đăng nhập',
  'logging_in': 'Đang đăng nhập...',
  'login_failed': 'Đăng nhập thất bại',
  'username': 'Tên đăng nhập',
  'password': 'Mật khẩu',
  'or': 'HOẶC',
  'go_to_account_center': 'Đến đăng nhập nền tảng dữ liệu',
  'account_center_ticket_missing': 'Thiếu vé đăng nhập trung tâm tài khoản. Vui lòng mở lại ArcReel từ nền tảng dữ liệu.',
  'account_center_login_failed': 'Không thể đăng nhập bằng trung tâm tài khoản',
  'account_center_signing_in': 'Đang xác minh danh tính trung tâm tài khoản…',
  'back_to_login': 'Quay lại đăng nhập',
  'account_center_setup_failed': 'Không thể hoàn tất thiết lập tài khoản',
  'first_account_center_login': 'Lần đầu đăng nhập qua trung tâm tài khoản',
  'loading_account_info': 'Đang tải thông tin tài khoản…',
  'auto_create_account': 'Tạo tài khoản ArcReel mới',
  'auto_create_account_desc': 'Tạo hồ sơ ArcReel cho danh tính này và chỉ truy cập qua nền tảng dữ liệu.',
  'create_and_enter': 'Tạo và truy cập',
  'bind_existing_account': 'Liên kết tài khoản hiện có',
  'bind_existing_account_desc': 'Xác minh tên đăng nhập và mật khẩu ArcReel một lần để giữ dữ liệu lịch sử.',
  'verify_bind_and_enter': 'Xác minh, liên kết và truy cập',
} satisfies Record<keyof typeof enAuth, string>;
