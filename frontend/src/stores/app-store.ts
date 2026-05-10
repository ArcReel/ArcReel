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

interface FocusedContext {
  type: "character" | "scene" | "prop" | "segment";
  id: string;
}

const ALL_ENTITIES_REVISION_KEY = "__all__";

export const ASSISTANT_PANEL_DEFAULT_WIDTH = 505;
export const ASSISTANT_PANEL_MIN_WIDTH = 360;
export const ASSISTANT_PANEL_MAX_WIDTH = 720;
export const ASSISTANT_PANEL_WIDTH_STORAGE_KEY = "arcreel_assistant_panel_width";

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
  pushToast: (text: string, tone?: Toast["tone"]) => void;
  pushNotification: (
    text: string,
    tone?: Toast["tone"],
    options?: { target?: WorkspaceNotificationTarget | null },
  ) => void;
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
  assistantPanelWidth: number;
  setAssistantPanelWidth: (width: number) => void;
  persistAssistantPanelWidth: () => void;
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

/**
 * 通知系统分工规则（issue #351）：
 *
 * - pushToast(text, tone)
 *     用于：用户主动操作的即时反馈。
 *     典型：表单保存/校验、导入/删除/切换/上传成功、scroll target 未找到、
 *          后台任务提交成功回执（task_submitted）、轻量错误提示。
 *
 * - pushWorkspaceNotification({ text, tone, target })
 *     用于：后台异步产生的事件，用户可能不在当前页。
 *     典型：SSE 单条事件留痕（如 agent_update_scene）。
 *
 * - pushNotification(text, tone, options?)
 *     用于：用户需要后续回看的重要结果。
 *     典型：后台任务失败（剪映/ZIP 导出失败、项目 regenerate 失败、
 *          参考生视频失败、storyboard/video/character/scene/prop 生成失败）、
 *          SSE grouped_notification。
 *
 * 判断口诀：
 *   1. 用户现在不在场 → 需要持久
 *   2. 后台任务"失败"需要留痕排查 → 需要持久
 *   3. 其余 → 仅 toast
 *
 * pushToast 不接受 { persist: true } 之类逃生门，强制调用点三选一，意图显式。
 */
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
  pushToast: (text, tone = "info") =>
    set({
      toast: { id: `${Date.now()}-${Math.random()}`, text, tone },
    }),
  pushNotification: (text, tone = "info", options) =>
    set((s) => ({
      toast: { id: `${Date.now()}-${Math.random()}`, text, tone },
      workspaceNotifications: [
        buildWorkspaceNotification({ text, tone, target: options?.target ?? null }),
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
