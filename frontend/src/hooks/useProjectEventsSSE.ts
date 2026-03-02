import { startTransition, useCallback, useEffect, useRef } from "react";
import { useLocation } from "wouter";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type {
  ProjectChange,
  ProjectChangeBatchPayload,
  WorkspaceNotificationTarget,
} from "@/types";

const CHANGE_PRIORITY: Record<string, number> = {
  "segment:updated": 0,
  "character:created": 1,
  "character:updated": 2,
  "clue:created": 3,
  "clue:updated": 4,
  "episode:created": 5,
  "episode:updated": 6,
  storyboard_ready: 7,
  video_ready: 8,
};

function getChangePriority(change: ProjectChange): number {
  if (change.action === "storyboard_ready" || change.action === "video_ready") {
    return CHANGE_PRIORITY[change.action] ?? Number.MAX_SAFE_INTEGER;
  }
  return CHANGE_PRIORITY[`${change.entity_type}:${change.action}`] ?? Number.MAX_SAFE_INTEGER;
}

function isNavigableChange(change: ProjectChange): boolean {
  if (change.action === "storyboard_ready" || change.action === "video_ready") {
    return false;
  }
  return Boolean(change.focus?.anchor_type && change.focus?.anchor_id);
}

function selectPrimaryChange(changes: ProjectChange[]): ProjectChange | null {
  return changes
    .filter((change) => isNavigableChange(change))
    .sort((left, right) => getChangePriority(left) - getChangePriority(right))[0] ?? null;
}

function isNotificationOnlyChange(change: ProjectChange): boolean {
  if (change.action === "storyboard_ready" || change.action === "video_ready") {
    return true;
  }
  return (
    change.important &&
    !change.focus?.anchor_type &&
    (change.entity_type === "character" || change.entity_type === "clue")
  );
}

function selectNotificationChange(changes: ProjectChange[]): ProjectChange | null {
  return changes
    .filter((change) => isNotificationOnlyChange(change))
    .sort((left, right) => getChangePriority(left) - getChangePriority(right))[0] ?? null;
}

function buildNotificationTarget(change: ProjectChange): WorkspaceNotificationTarget | null {
  const focus = change.focus;
  if (!focus?.anchor_type || !focus.anchor_id) return null;

  let route = "";
  if (focus.pane === "characters") {
    route = "/characters";
  } else if (focus.pane === "clues") {
    route = "/clues";
  } else if (focus.pane === "episode" && typeof focus.episode === "number") {
    route = `/episodes/${focus.episode}`;
  }

  if (!route) return null;

  return {
    type: focus.anchor_type,
    id: focus.anchor_id,
    route,
    highlight_style: "flash",
  };
}

function formatDeferredText(change: ProjectChange): string {
  if (change.action === "storyboard_ready") {
    return `AI 刚生成了 ${change.label} 的分镜图，点击查看`;
  }
  if (change.action === "video_ready") {
    return `AI 刚生成了 ${change.label} 的视频，点击查看`;
  }
  if (change.action === "created") {
    return `AI 刚新增了 ${change.label}，点击查看`;
  }
  return `AI 刚更新了 ${change.label}，点击查看`;
}

function formatNotificationText(change: ProjectChange): string {
  if (change.action === "storyboard_ready") {
    return `${change.label}的分镜图已生成`;
  }
  if (change.action === "video_ready") {
    return `${change.label}的视频已生成`;
  }
  return `${change.label} 已更新`;
}

function isWorkspaceEditing(): boolean {
  const active = document.activeElement;
  if (active instanceof HTMLElement) {
    const tagName = active.tagName.toLowerCase();
    if (tagName === "input" || tagName === "textarea" || tagName === "select") {
      return true;
    }
    if (active.isContentEditable) {
      return true;
    }
  }
  return Boolean(document.querySelector("[data-workspace-editing='true']"));
}

export function useProjectEventsSSE(projectName?: string | null): void {
  const [, setLocation] = useLocation();
  const setCurrentProject = useProjectsStore((s) => s.setCurrentProject);
  const invalidateMediaAssets = useAppStore((s) => s.invalidateMediaAssets);
  const triggerScrollTo = useAppStore((s) => s.triggerScrollTo);
  const clearScrollTarget = useAppStore((s) => s.clearScrollTarget);
  const pushToast = useAppStore((s) => s.pushToast);
  const pushWorkspaceNotification = useAppStore((s) => s.pushWorkspaceNotification);
  const clearWorkspaceNotifications = useAppStore((s) => s.clearWorkspaceNotifications);
  const setAssistantToolActivitySuppressed = useAppStore(
    (s) => s.setAssistantToolActivitySuppressed
  );

  const sourceRef = useRef<EventSource | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastFingerprintRef = useRef<string | null>(null);
  const refreshingRef = useRef(false);
  const needsRefreshRef = useRef(false);
  const queuedFocusRef = useRef<WorkspaceNotificationTarget | null>(null);

  const executeFocus = useCallback(
    (target: WorkspaceNotificationTarget) => {
      startTransition(() => {
        setLocation(target.route);
      });
      triggerScrollTo({
        type: target.type,
        id: target.id,
        route: target.route,
        highlight_style: target.highlight_style ?? "flash",
        expires_at: Date.now() + 3000,
      });
    },
    [setLocation, triggerScrollTo],
  );

  const flushQueuedFocus = useCallback(() => {
    const target = queuedFocusRef.current;
    if (!target) return;
    queuedFocusRef.current = null;
    if (isWorkspaceEditing()) {
      return;
    }
    executeFocus(target);
  }, [executeFocus]);

  const refreshProject = useCallback(async () => {
    if (!projectName) return;
    if (refreshingRef.current) {
      needsRefreshRef.current = true;
      return;
    }

    refreshingRef.current = true;
    try {
      const res = await API.getProject(projectName);
      setCurrentProject(projectName, res.project, res.scripts ?? {});
      invalidateMediaAssets();
    } catch (err) {
      pushToast(`同步项目变更失败: ${(err as Error).message}`, "warning");
    } finally {
      refreshingRef.current = false;
      if (needsRefreshRef.current) {
        needsRefreshRef.current = false;
        void refreshProject();
        return;
      }
      flushQueuedFocus();
    }
  }, [flushQueuedFocus, invalidateMediaAssets, projectName, pushToast, setCurrentProject]);

  useEffect(() => {
    lastFingerprintRef.current = null;
    queuedFocusRef.current = null;
    needsRefreshRef.current = false;
    refreshingRef.current = false;
    clearScrollTarget();
    clearWorkspaceNotifications();
    return () => {
      queuedFocusRef.current = null;
      clearScrollTarget();
      clearWorkspaceNotifications();
    };
  }, [clearScrollTarget, clearWorkspaceNotifications, projectName]);

  useEffect(() => {
    if (!projectName) return;
    let disposed = false;

    const connect = () => {
      if (sourceRef.current) {
        sourceRef.current.close();
        sourceRef.current = null;
      }

      const source = API.openProjectEventStream({
        projectName,
        onSnapshot(payload) {
          if (disposed) return;
          const previousFingerprint = lastFingerprintRef.current;
          lastFingerprintRef.current = payload.fingerprint;
          if (previousFingerprint && previousFingerprint !== payload.fingerprint) {
            void refreshProject();
          }
        },
        onChanges(payload: ProjectChangeBatchPayload) {
          if (disposed) return;
          lastFingerprintRef.current = payload.fingerprint;
          setAssistantToolActivitySuppressed(true);

          const notificationChange = selectNotificationChange(payload.changes);
          if (notificationChange) {
            pushToast(formatNotificationText(notificationChange), "success");
          }

          if (payload.source !== "webui") {
            const primaryChange = selectPrimaryChange(payload.changes);
            const focusTarget = primaryChange
              ? buildNotificationTarget(primaryChange)
              : null;
            if (primaryChange && focusTarget) {
              pushWorkspaceNotification({
                text: formatDeferredText(primaryChange),
                target: focusTarget,
              });
              if (isWorkspaceEditing()) {
                queuedFocusRef.current = null;
              } else {
                queuedFocusRef.current = focusTarget;
              }
            }
          }

          void refreshProject();
        },
        onError() {
          if (disposed) return;
          if (sourceRef.current) {
            sourceRef.current.close();
            sourceRef.current = null;
          }
          reconnectTimerRef.current = setTimeout(() => {
            if (!disposed) connect();
          }, 3000);
        },
      });

      sourceRef.current = source;
    };

    connect();

    return () => {
      disposed = true;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (sourceRef.current) {
        sourceRef.current.close();
        sourceRef.current = null;
      }
    };
  }, [
    clearWorkspaceNotifications,
    projectName,
    pushWorkspaceNotification,
    refreshProject,
    pushToast,
    setAssistantToolActivitySuppressed,
  ]);
}
