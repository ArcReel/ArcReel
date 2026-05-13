import { create } from "zustand";
import { getToken, setToken as saveToken, clearToken } from "@/utils/auth";

interface AuthState {
  token: string | null;
  username: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  initialize: () => void;
  login: (token: string, username: string) => void;
  logout: () => void;
  setLoading: (loading: boolean) => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  username: null,
  isAuthenticated: false,
  isLoading: true,

  initialize: () => {
    const token = getToken();
    if (token) {
      set({ token, isAuthenticated: true, isLoading: false });
      return;
    }
    // 无 token 时先问后端是否启用了鉴权。`AUTH_ENABLED=false`（桌面壳 / 单
    // 用户场景）下后端全链路 bypass，前端也应该跳过登录页直接进主界面。
    // 网络异常时退回到原有行为（显示登录页），保守优先。
    fetch("/api/v1/auth/status")
      .then(async (res) => {
        if (!res.ok) throw new Error(`status ${res.status}`);
        const data: { enabled: boolean } = await res.json();
        if (!data.enabled) {
          set({ isAuthenticated: true, isLoading: false });
        } else {
          set({ isLoading: false });
        }
      })
      .catch((err) => {
        console.warn("[auth] /auth/status fetch failed; defaulting to login", err);
        set({ isLoading: false });
      });
  },

  login: (token, username) => {
    saveToken(token);
    set({ token, username, isAuthenticated: true, isLoading: false });
  },

  logout: () => {
    clearToken();
    set({ token: null, username: null, isAuthenticated: false });
  },

  setLoading: (isLoading) => set({ isLoading }),
}));
