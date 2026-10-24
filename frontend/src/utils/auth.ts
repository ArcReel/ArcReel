const TOKEN_KEY = "arcreel_auth_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  // react-doctor-disable-next-line react-doctor/auth-token-in-web-storage -- 后端为 OAuth2 Bearer，浏览器外的客户端共用同一 token；改 httpOnly cookie 需另建 cookie 认证与 CSRF 防护，XSS 防线在输出转义与 CSP
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export function getAuthHeader(): string | null {
  const token = getToken();
  return token ? `Bearer ${token}` : null;
}
