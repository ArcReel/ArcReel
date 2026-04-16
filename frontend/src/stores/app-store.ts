import { create } from "zustand";
import type {
  WorkspaceFocusTarget,
  WorkspaceFocusTargetInput,
  WorkspaceNotification,
  WorkspaceNotificationInput,
  WorkspaceNotificationTarget,
} from "@/types";

interface Toast {
  id: string;
  text: string;
  tone: "info" | "success" | "error" | "warning";
}

interface ToastOptions {
  target?: WorkspaceNotificationTarget | null;
}

interface FocusedContext {
  type: "character" | "scene" | "prop" | "segment";
  id: string;
}

const ALL_ENTITIES_REVISION_KEY = "__all__";

interface AppState {
  // Context focus (design doc "Context-Aware" feature)
  focusedContext: FocusedContext | null;
  setFocusedContext: (ctx: FocusedContext | null) => void;

  // Scroll targeting (Agent-triggered)
  scrollTarget: WorkspaceFocusTarget | null;
  triggerScrollTo: (target: WorkspaceFocusTargetInput) => void;
  clearScrollTarget: (requestId?: string) => void;
  assistantToolActivitySuppressed: boolean;
  setAssistantToolActivitySuppressed: (suppressed: boolean) => void;

  // Toast
  toast: Toast | null;
  pushToast: (text: string, tone?: Toast["tone"], options?: ToastOptions) => void;
  clearToast: () => void;
  workspaceNotifications: WorkspaceNotification[];
  pushWorkspaceNotification: (input: WorkspaceNotificationInput) => void;
  markWorkspaceNotificationRead: (id: string) => void;
  markAllWorkspaceNotificationsRead: () => void;
  removeWorkspaceNotification: (id: string) => void;
  clearWorkspaceNotifications: () => void;

  // Panels
  assistantPanelOpen: boolean;
  toggleAssistantPanel: () => void;
  setAssistantPanelOpen: (open: boolean) => void;
  taskHudOpen: boolean;
  setTaskHudOpen: (open: boolean) => void;

  // Source files invalidation signal
  sourceFilesVersion: number;
  invalidateSourceFiles: () => void;

  // Grid list invalidation signal (incremented on grid_ready SSE events)
  gridsRevision: number;
  invalidateGrids: () => void;

  // Entity-scoped invalidation signal for cache-busted asset URLs
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
  focusedContext: null,
  setFocusedContext: (ctx) => set({ focusedContext: ctx }),

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
  assistantToolActivitySuppressed: false,
  setAssistantToolActivitySuppressed: (suppressed) =>
    set({ assistantToolActivitySuppressed: suppressed }),

  toast: null,
  pushToast: (text, tone = "info", options) =>
    set((s) => ({
      toast: { id: `${Date.now()}-${Math.random()}`, text, tone },
      workspaceNotifications: [
        buildWorkspaceNotification({
          text,
          tone,
          target: options?.target ?? null,
        }),
        ...s.workspaceNotifications,
      ].slice(0, MAX_WORKSPACE_NOTIFICATIONS),
    })),
  clearToast: () => set({ toast: null }),
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

  assistantPanelOpen: true,
  toggleAssistantPanel: () =>
    set((s) => ({ assistantPanelOpen: !s.assistantPanelOpen })),
  setAssistantPanelOpen: (open) => set({ assistantPanelOpen: open }),
  taskHudOpen: false,
  setTaskHudOpen: (open) => set({ taskHudOpen: open }),

  sourceFilesVersion: 0,
  invalidateSourceFiles: () => set((s) => ({ sourceFilesVersion: s.sourceFilesVersion + 1 })),

  gridsRevision: 0,
  invalidateGrids: () => set((s) => ({ gridsRevision: s.gridsRevision + 1 })),

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
