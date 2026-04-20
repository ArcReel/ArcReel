import type enErrors from '../en/errors';

export default {
  'unknown_error': '发生未知错误',
  'network_error': '网络错误，请检查网络连接',
  'unauthorized': '未授权，请重新登录',
  'forbidden': '无权访问',
  'not_found': '资源不存在',
  'server_error': '服务器内部错误，请稍后再试',
  'validation_error': '表单验证失败',
  'source_unsupported_format': '不支持的源文件格式：{{ext}}',
  'source_decode_failed': '源文件「{{filename}}」解码失败（已尝试：{{tried}}）',
  'source_corrupt_file': '源文件「{{filename}}」无法解析：{{reason}}',
  'source_too_large': '源文件「{{filename}}」过大（{{size_mb}} MB > {{limit_mb}} MB）',
  'source_conflict': '源文件「{{existing}}」已存在',
} satisfies Record<keyof typeof enErrors, string>;
