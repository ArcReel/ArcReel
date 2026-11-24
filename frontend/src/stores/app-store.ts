import { create } from "zustand";
import type {
  PlaybackStartRequest,
  WorkspaceFocusTarget,
  WorkspaceFocusTargetInput,
  WorkspaceNotification,
  WorkspaceNotificationInput,
  WorkspaceNotificationTarget,
} from "@/types";

interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface Toast {
  id: string;
  text: string;
  tone: "info" | "success" | "error" | "warning";
  action?: ToastAction;
}

const PLAYBACK_START_TTL_MS = 8000;
const ALL_ENTITIES_REVISION_KEY = "__all__";

export const ASSISTANT_PANEL_DEFAULT_WIDTH = 420;
export const ASSISTANT_PANEL_MIN_WIDTH = 320;
export const ASSISTANT_PANEL_MAX_WIDTH = 640;
const ASSISTANT_PANEL_WIDTH_STORAGE_KEY = "arcreel_assistant_panel_width";
const ASSISTANT_PANEL_OPEN_STORAGE_KEY = "arcreel_assistant_panel_open";

export function clampAssistantPanelWidth(value: number): number {
  if (!Number.isFinite(value)) return ASSISTANT_PANEL_DEFAULT_WIDTH;
  return Math.min(
    ASSISTANT_PANEL_MAX_WIDTH,
    Math.max(ASSISTANT_PANEL_MIN_WIDTH, Math.round(value)),
  );
}

function readPersistedAssistantPanelWidth(): number {
  if (typeof window === "undefined") return ASSISTANT_PANEL_DEFAULT_WIDTH;
  try {
    const raw = window.localStorage.getItem(ASSISTANT_PANEL_WIDTH_STORAGE_KEY);
    if (!raw) return ASSISTANT_PANEL_DEFAULT_WIDTH;
    const parsed = parseInt(raw, 10);
    if (Number.isNaN(parsed)) return ASSISTANT_PANEL_DEFAULT_WIDTH;
    return clampAssistantPanelWidth(parsed);
  } catch {
    return ASSISTANT_PANEL_DEFAULT_WIDTH;
  }
}

function readPersistedAssistantPanelOpen(): boolean | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(ASSISTANT_PANEL_OPEN_STORAGE_KEY);
    return raw === "true" ? true : raw === "false" ? false : null;
  } catch {
    return null;
  }
}

function persistAssistantPanelOpen(open: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(ASSISTANT_PANEL_OPEN_STORAGE_KEY, String(open));
  } catch {
    // localStorage 不可用时仅保留内存值
  }
}

interface AppState {
  scrollTarget: WorkspaceFocusTarget | null;
  triggerScrollTo: (target: WorkspaceFocusTargetInput) => void;
  clearScrollTarget: (requestId?: string) => void;
  playbackStart: PlaybackStartRequest | null;
  requestPlaybackStart: (input: Omit<PlaybackStartRequest, "request_id">) => void;
  clearPlaybackStart: (requestId?: string) => void;
  assistantToolActivitySuppressed: boolean;
  setAssistantToolActivitySuppressed: (suppressed: boolean) => void;

  toast: Toast | null;
  pushToast: (text: string, tone?: Toast["tone"], options?: { action?: ToastAction }) => void;
  pushNotification: (
    text: string,
    tone?: Toast["tone"],
    options?: { target?: WorkspaceNotificationTarget | null },
  ) => void;
  workspaceNotifications: WorkspaceNotification[];
  pushWorkspaceNotification: (input: WorkspaceNotificationInput) => void;
  markWorkspaceNotificationRead: (id: string) => void;
  markAllWorkspaceNotificationsRead: () => void;
  removeWorkspaceNotification: (id: string) => void;
  clearWorkspaceNotifications: () => void;

  assistantPanelOpen: boolean;
  toggleAssistantPanel: () => void;
  setAssistantPanelOpen: (open: boolean) => void;
  assistantPanelWidth: number;
  setAssistantPanelWidth: (width: number) => void;
  persistAssistantPanelWidth: () => void;
  usagePanelOpen: boolean;
  setUsagePanelOpen: (open: boolean) => void;

  gridsRevision: number;
  invalidateGrids: () => void;

  trashRevision: number;
  invalidateTrash: () => void;

  referenceVideoUnitsRevision: number;
  invalidateReferenceVideoUnits: () => void;

  entityRevisions: Record<string, number>;
  invalidateEntities: (keys: string[]) => void;
  invalidateAllEntities: () => void;
  getEntityRevision: (key: string) => number;
}

const MAX_WORKSPACE_NOTIFICATIONS = 40;

function buildWorkspaceNotification(
  input: WorkspaceNotificationInput,
): WorkspaceNotification {
  return {
    id: `${Date.now()}-${Math.random()}`,
    text: input.text,
    tone: input.tone ?? "info",
    created_at: Date.now(),
    read: input.read ?? false,
    target: input.target ?? null,
  };
}

export const useAppStore = create<AppState>((set, get) => ({
  scrollTarget: null,
  triggerScrollTo: (target) =>
    set({
      scrollTarget: {
        request_id: target.request_id ?? `${Date.now()}-${Math.random()}`,
        type: target.type,
        id: target.id,
        route: target.route ?? "",
        highlight: true,
        highlight_style: target.highlight_style ?? "flash",
        expires_at: target.expires_at ?? Date.now() + 3000,
      },
    }),
  clearScrollTarget: (requestId) =>
    set((s) => {
      if (!requestId || s.scrollTarget?.request_id === requestId) {
        return { scrollTarget: null };
      }
      return s;
    }),
  playbackStart: null,
  requestPlaybackStart: (input) => {
    const requestId = `${Date.now()}-${Math.random()}`;
    set({ playbackStart: { ...input, request_id: requestId } });
    setTimeout(() => get().clearPlaybackStart(requestId), PLAYBACK_START_TTL_MS);
  },
  clearPlaybackStart: (requestId) =>
    set((s) => {
      if (!requestId || s.playbackStart?.request_id === requestId) {
        return { playbackStart: null };
      }
      return s;
    }),
  assistantToolActivitySuppressed: false,
  setAssistantToolActivitySuppressed: (suppressed) =>
    set({ assistantToolActivitySuppressed: suppressed }),

  toast: null,
  pushToast: (text, tone = "info", options) =>
    set({
      toast: { id: `${Date.now()}-${Math.random()}`, text, tone, action: options?.action },
    }),
  pushNotification: (text, tone = "info", options) =>
    set((s) => ({
      toast: { id: `${Date.now()}-${Math.random()}`, text, tone },
      workspaceNotifications: [
        buildWorkspaceNotification({ text, tone, target: options?.target ?? null }),
        ...s.workspaceNotifications,
      ].slice(0, MAX_WORKSPACE_NOTIFICATIONS),
    })),
  workspaceNotifications: [],
  pushWorkspaceNotification: (input) =>
    set((s) => ({
      workspaceNotifications: [
        buildWorkspaceNotification(input),
        ...s.workspaceNotifications,
      ].slice(0, MAX_WORKSPACE_NOTIFICATIONS),
    })),
  markWorkspaceNotificationRead: (id) =>
    set((s) => ({
      workspaceNotifications: s.workspaceNotifications.map((item) =>
        item.id === id ? { ...item, read: true } : item
      ),
    })),
  markAllWorkspaceNotificationsRead: () =>
    set((s) => ({
      workspaceNotifications: s.workspaceNotifications.map((item) =>
        item.read ? item : { ...item, read: true }
      ),
    })),
  removeWorkspaceNotification: (id) =>
    set((s) => ({
      workspaceNotifications: s.workspaceNotifications.filter((item) => item.id !== id),
    })),
  clearWorkspaceNotifications: () => set({ workspaceNotifications: [] }),

  assistantPanelOpen: readPersistedAssistantPanelOpen() ?? true,
  toggleAssistantPanel: () =>
    set((s) => {
      const open = !s.assistantPanelOpen;
      persistAssistantPanelOpen(open);
      return { assistantPanelOpen: open };
    }),
  setAssistantPanelOpen: (open) => set({ assistantPanelOpen: open }),
  assistantPanelWidth: readPersistedAssistantPanelWidth(),
  setAssistantPanelWidth: (width) =>
    set({ assistantPanelWidth: clampAssistantPanelWidth(width) }),
  persistAssistantPanelWidth: () => {
    if (typeof window === "undefined") return;
    const width = clampAssistantPanelWidth(get().assistantPanelWidth);
    try {
      window.localStorage.setItem(
        ASSISTANT_PANEL_WIDTH_STORAGE_KEY,
        String(width),
      );
    } catch {
      // localStorage 不可用（隐私模式 / quota exceeded）静默失败，内存值仍生效
    }
  },
  usagePanelOpen: false,
  setUsagePanelOpen: (open) => set({ usagePanelOpen: open }),

  gridsRevision: 0,
  invalidateGrids: () => set((s) => ({ gridsRevision: s.gridsRevision + 1 })),

  trashRevision: 0,
  invalidateTrash: () => set((s) => ({ trashRevision: s.trashRevision + 1 })),

  referenceVideoUnitsRevision: 0,
  invalidateReferenceVideoUnits: () =>
    set((s) => ({ referenceVideoUnitsRevision: s.referenceVideoUnitsRevision + 1 })),

  entityRevisions: {},
  invalidateEntities: (keys) =>
    set((s) => {
      const normalizedKeys = [...new Set(keys.filter(Boolean))];
      if (normalizedKeys.length === 0) {
        return s;
      }

      const entityRevisions = { ...s.entityRevisions };
      for (const key of normalizedKeys) {
        entityRevisions[key] = (entityRevisions[key] ?? 0) + 1;
      }
      return { entityRevisions };
    }),
  invalidateAllEntities: () =>
    set((s) => ({
      entityRevisions: {
        ...s.entityRevisions,
        [ALL_ENTITIES_REVISION_KEY]:
          (s.entityRevisions[ALL_ENTITIES_REVISION_KEY] ?? 0) + 1,
      },
    })),
  getEntityRevision: (key) => {
    const entityRevisions = get().entityRevisions;
    return (
      (entityRevisions[key] ?? 0) +
      (entityRevisions[ALL_ENTITIES_REVISION_KEY] ?? 0)
    );
  },
}));
