const TOKEN_KEY = "arcreel_auth_token";
const USER_KEY = "arcreel_auth_user";

export interface StoredAuthUser {
  id: string;
  username: string;
  role: string;
  displayName: string | null;
  identitySource: string;
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

export function setStoredUser(user: StoredAuthUser): void {
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function getStoredUser(): StoredAuthUser | null {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<StoredAuthUser>;
    if (!parsed.id || !parsed.username || !parsed.role) return null;
    return {
      id: parsed.id,
      username: parsed.username,
      role: parsed.role,
      displayName: parsed.displayName ?? null,
      identitySource: parsed.identitySource ?? "internal",
    };
  } catch {
    return null;
  }
}

export function getAuthHeader(): string | null {
  const token = getToken();
  return token ? `Bearer ${token}` : null;
}
