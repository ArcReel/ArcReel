import type enAuth from '../en/auth';

export default {
  'login': 'Войти',
  'logging_in': 'Вход…',
  'login_failed': 'Не удалось войти',
  'username': 'Имя пользователя',
  'password': 'Пароль',
  'login_subtitle': 'Войдите в свою учётную запись',
} satisfies Record<keyof typeof enAuth, string> & Record<string, string>;
