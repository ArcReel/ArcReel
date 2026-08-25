import { create } from "zustand";
import {
  clearToken,
  getStoredUser,
  getToken,
  setStoredUser,
  setToken as saveToken,
} from "@/utils/auth";

interface AuthState {
  token: string | null;
  username: string | null;
  userId: string | null;
  role: string | null;
  displayName: string | null;
  identitySource: string | null;
  accountCenterEnabled: boolean;
  isAuthenticated: boolean;
  isLoading: boolean;
  initialize: () => void;
  login: (
    token: string,
    username: string,
    role?: string,
    userId?: string,
    displayName?: string | null,
    identitySource?: string,
  ) => void;
  logout: () => void;
  setLoading: (loading: boolean) => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  username: null,
  userId: null,
  role: null,
  displayName: null,
  identitySource: null,
  accountCenterEnabled: false,
  isAuthenticated: false,
  isLoading: true,

  initialize: () => {
    const token = getToken();
    if (token) {
      const user = getStoredUser();
      set({
        token,
        username: user?.username ?? null,
        userId: user?.id ?? "default",
        role: user?.role ?? "admin",
        displayName: user?.displayName ?? null,
        identitySource: user?.identitySource ?? "internal",
        isAuthenticated: true,
        isLoading: false,
      });
      return;
    }
    // 无 token 时先问后端是否启用了鉴权。`AUTH_ENABLED=false` 时后端全链路
    // bypass，前端也应该跳过登录页直接进主界面。超时 / 网络异常 / 响应 shape
    // 异常时 fail-closed 退回到登录页，避免误把损坏响应当成"无需鉴权"放行。
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 5000);
    fetch("/api/v1/auth/status", { signal: controller.signal })
      .then(async (res) => {
        if (!res.ok) throw new Error(`status ${res.status}`);
        const payload: unknown = await res.json();
        if (
          typeof payload !== "object" ||
          payload === null ||
          typeof (payload as { enabled?: unknown }).enabled !== "boolean"
        ) {
          throw new Error("invalid /auth/status payload");
        }
        const { enabled, account_center_enabled: accountCenterEnabled } = payload as {
          enabled: boolean;
          account_center_enabled?: boolean;
        };
        set({ accountCenterEnabled: Boolean(accountCenterEnabled) });
        if (!enabled) {
          set({ isAuthenticated: true });
        }
      })
      .catch((err) => {
        console.warn("[auth] /auth/status fetch failed; defaulting to login", err);
      })
      .finally(() => {
        clearTimeout(timeoutId);
        set({ isLoading: false });
      });
  },

  login: (token, username, role = "admin", userId = "default", displayName = null, identitySource = "internal") => {
    saveToken(token);
    setStoredUser({ id: userId, username, role, displayName, identitySource });
    set({
      token,
      username,
      userId,
      role,
      displayName,
      identitySource,
      isAuthenticated: true,
      isLoading: false,
    });
  },

  logout: () => {
    clearToken();
    set({
      token: null,
      username: null,
      userId: null,
      role: null,
      displayName: null,
      identitySource: null,
      isAuthenticated: false,
    });
  },

  setLoading: (isLoading) => set({ isLoading }),
}));
