import { create } from "zustand";
import { getToken, setToken as saveToken, clearToken } from "@/utils/auth";

interface AuthState {
  token: string | null;
  username: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  /** 后端 ``AUTH_ENABLED`` 状态；未取到（请求中或失败）时为 null。 */
  authEnabled: boolean | null;
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
  authEnabled: null,

  initialize: () => {
    // 无论有无 token 都查询后端是否启用了鉴权：结果写入 authEnabled 供全局提示条使用。
    // 有 token 时直接进入主界面，查询在后台完成，不阻塞首屏。
    // 无 token 时等待查询结果：`AUTH_ENABLED=false` 时后端全链路 bypass，前端也
    // 跳过登录页直接进主界面。超时 / 网络异常 / 响应 shape 异常时 fail-closed
    // 退回到登录页，避免误把损坏响应当成"无需鉴权"放行。
    const token = getToken();
    if (token) {
      set({ token, isAuthenticated: true, isLoading: false });
    }
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
        const { enabled } = payload as { enabled: boolean };
        set(enabled ? { authEnabled: true } : { authEnabled: false, isAuthenticated: true });
      })
      .catch((err) => {
        console.warn("[auth] /auth/status fetch failed; auth state unknown", err);
      })
      .finally(() => {
        clearTimeout(timeoutId);
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
