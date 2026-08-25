import type enAuth from '../en/auth';

export default {
  'login': '登录',
  'logging_in': '登录中...',
  'login_failed': '登录失败',
  'username': '用户名',
  'password': '密码',
  'or': '或者',
  'go_to_account_center': '去登录数据中台',
  'account_center_ticket_missing': '缺少中台登录票据，请重新从数据中台进入 ArcReel。',
  'account_center_login_failed': '无法完成中台登录',
  'account_center_signing_in': '正在验证中台身份…',
  'back_to_login': '返回登录页',
  'account_center_setup_failed': '无法完成账号设置',
  'first_account_center_login': '首次进入，请选择账号接入方式',
  'loading_account_info': '正在读取账号信息…',
  'auto_create_account': '直接进入（推荐）',
  'auto_create_account_desc': '无需再次输入密码，系统会为当前中台身份自动分配独立账号，后续从数据中台即可直接进入。',
  'create_and_enter': '自动分配并进入',
  'bind_existing_account': '绑定已有 ArcReel 账号',
  'bind_existing_account_desc': '只需验证一次原 ArcReel 账号密码，即可继续使用该账号下的历史数据。',
  'verify_bind_and_enter': '验证、绑定并进入',
} satisfies Record<keyof typeof enAuth, string>;
