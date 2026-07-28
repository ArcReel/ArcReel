import type enErrors from "@/i18n/en/errors";

export default {
  'unknown_error': 'Đã xảy ra lỗi không xác định',
  'network_error': 'Lỗi mạng, vui lòng kiểm tra kết nối',
  'unauthorized': 'Chưa xác thực, vui lòng đăng nhập lại',
  'forbidden': 'Không có quyền truy cập',
  'not_found': 'Không tìm thấy tài nguyên',
  'server_error': 'Lỗi máy chủ, vui lòng thử lại sau',
  'validation_error': 'Xác thực dữ liệu thất bại',
  'source_unsupported_format': 'Định dạng nguồn không hỗ trợ: {{ext}}',
  'source_decode_failed': 'Không giải mã được "{{filename}}" (đã thử: {{tried}})',
  'source_corrupt_file': 'Tệp nguồn "{{filename}}" không thể phân tích: {{reason}}',
  'source_too_large': 'Tệp nguồn "{{filename}}" quá lớn ({{size_mb}} MB > {{limit_mb}} MB)',
  'source_conflict': 'Tệp nguồn "{{existing}}" đã tồn tại',
  // Image Capability
  'image_endpoint_mismatch_no_i2i': 'Mô hình {{model}} chỉ hỗ trợ text-to-image (không có /v1/images/edits)',
  'image_endpoint_mismatch_no_t2i': 'Mô hình {{model}} chỉ hỗ trợ image-to-image (yêu cầu ảnh tham chiếu)',
  'image_capability_missing_i2i': '{{provider}}/{{model}} không hỗ trợ image-to-image; hãy cấu hình mô hình mặc định có hỗ trợ chỉnh sửa ảnh',
  'image_capability_missing_t2i': '{{provider}}/{{model}} không hỗ trợ text-to-image; hãy cấu hình mô hình mặc định có hỗ trợ text-to-image',
} satisfies Record<keyof typeof enErrors, string>;
