import { create } from "zustand";
import { getToken, setToken as saveToken, clearToken } from "@/utils/auth";
import { usePermissionsStore } from "@/stores/fork-permissions-store"; // fork-private

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
    } else {
      set({ isLoading: false });
    }
  },

  login: (token, username) => {
    saveToken(token);
    set({ token, username, isAuthenticated: true, isLoading: false });
    void usePermissionsStore.getState().fetchMe(); // fork-private
  },

  logout: () => {
    clearToken();
    set({ token: null, username: null, isAuthenticated: false });
    usePermissionsStore.getState().reset(); // fork-private
  },

  setLoading: (isLoading) => set({ isLoading }),
}));
