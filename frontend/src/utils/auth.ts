const TOKEN_KEY = "arcreel_auth_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  // react-doctor-disable-next-line react-doctor/auth-token-in-web-storage -- Backend uses OAuth2 Bearer tokens shared by non-browser clients; httpOnly cookies require separate cookie authentication and CSRF protection, while output escaping and CSP provide XSS defenses
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export function getAuthHeader(): string | null {
  const token = getToken();
  return token ? `Bearer ${token}` : null;
}
