// fork-private — current user role store
//
// Lightweight zustand store that mirrors `GET /api/v1/auth/me`.
// The auth-store handles the JWT lifecycle; this one specifically caches
// `role` so RoleGate / nav menus can render synchronously without prop drilling.

import { create } from 'zustand';
import { getToken } from '@/utils/auth';

export type Role = 'admin' | 'user';

export interface MeResponse {
  id: string;
  username: string;
  role: string;
}

interface PermissionsState {
  id: string | null;
  username: string | null;
  role: Role | null;
  loaded: boolean;
  fetchMe: () => Promise<void>;
  reset: () => void;
}

function normaliseRole(raw: string | null | undefined): Role {
  if (raw === 'admin' || raw === 'user') return raw;
  // Unknown → least privilege.
  return 'user';
}

export const usePermissionsStore = create<PermissionsState>((set) => ({
  id: null,
  username: null,
  role: null,
  loaded: false,

  fetchMe: async () => {
    const token = getToken();
    if (!token) {
      set({ id: null, username: null, role: null, loaded: true });
      return;
    }
    try {
      const resp = await fetch('/api/v1/auth/me', {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) {
        set({ id: null, username: null, role: null, loaded: true });
        return;
      }
      const body = (await resp.json()) as MeResponse;
      set({
        id: body.id,
        username: body.username,
        role: normaliseRole(body.role),
        loaded: true,
      });
    } catch {
      set({ id: null, username: null, role: null, loaded: true });
    }
  },

  reset: () => set({ id: null, username: null, role: null, loaded: false }),
}));

export function isAdmin(role: Role | null): boolean {
  return role === 'admin';
}
