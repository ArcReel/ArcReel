// fork-private — 用户角色相关界面文案
const fork = {
  access: {
    role: {
      admin: '管理员',
      user: '普通用户',
    },
    forbidden: {
      title: '无权访问',
      hint: '当前角色无法访问此页面。',
    },
    users: {
      nav: '用户管理',
      title: '用户管理',
      empty: '还没有用户，先在下方创建一个吧。',
      create: '新建用户',
      create_submit: '创建',
      column_username: '用户名',
      column_role: '角色',
      column_created: '创建时间',
      column_active: '启用',
      column_actions: '操作',
      delete: '删除',
      delete_confirm: '确定删除用户「{{name}}」吗？',
      username_placeholder: '3-32 位小写字母/数字/_/-',
      shared_password_hint: '本期所有用户共享 AUTH_PASSWORD 环境变量，暂不支持单独密码。',
      created_toast: '用户「{{name}}」已创建。',
      deleted_toast: '用户「{{name}}」已删除。',
    },
  },
};
export default fork;
