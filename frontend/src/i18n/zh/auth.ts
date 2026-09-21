import type enAuth from '../en/auth';

export default {
  'login': '登录',
  'logging_in': '登录中...',
  'login_failed': '登录失败',
  'username': '用户名',
  'auth_disabled_banner_title': '认证已关闭。',
  'auth_disabled_banner_body': '所有管理接口无需登录即可访问。仅适用于受独立网络边界保护的本机环境；远程部署请保持认证开启。',
  'password': '密码',
} satisfies Record<keyof typeof enAuth, string>;
