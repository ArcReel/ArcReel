import { startTransition, useCallback, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "wouter";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useCostStore } from "@/stores/cost-store";
import { errMsg } from "@/utils/async";
import type {
  ProjectChange,
  ProjectChangeBatchPayload,
  WorkspaceNotificationTarget,
} from "@/types";
import {
  buildEntityRevisionKey,
  formatGroupedDeferredText,
  formatGroupedNotificationText,
  groupChangesByType,
  type GroupedProjectChange,
} from "@/utils/project-changes";

const CHANGE_PRIORITY: Record<string, number> = {
  "segment:updated": 0,
  "character:created": 1,
  "character:updated": 2,
  "scene:created": 3,
  "scene:updated": 3.5,
  "prop:created": 4,
  "prop:updated": 4.5,
  "episode:created": 5,
  "episode:updated": 6,
  "draft:created": 6.5,
  storyboard_ready: 7,
  video_ready: 8,
  grid_ready: 9,
};

function getChangePriority(change: ProjectChange): number {
  if (change.action === "storyboard_ready" || change.action === "video_ready" || change.action === "grid_ready") {
    return CHANGE_PRIORITY[change.action] ?? Number.MAX_SAFE_INTEGER;
  }
  return CHANGE_PRIORITY[`${change.entity_type}:${change.action}`] ?? Number.MAX_SAFE_INTEGER;
}

function isNavigableChange(change: ProjectChange): boolean {
  if (change.action === "storyboard_ready" || change.action === "video_ready" || change.action === "grid_ready") {
    return false;
  }
  return Boolean(change.focus?.anchor_type && change.focus?.anchor_id);
}

function buildNotificationTarget(change: ProjectChange): WorkspaceNotificationTarget | null {
  const focus = change.focus;
  if (!focus?.anchor_type || !focus.anchor_id) return null;

  let route = "";
  if (focus.pane === "characters") {
    route = "/characters";
  } else if (focus.pane === "scenes") {
    route = "/scenes";
  } else if (focus.pane === "props") {
    route = "/props";
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

function getGroupPriority(group: GroupedProjectChange): number {
  return Math.min(
    ...group.changes.map((change) => getChangePriority(change)),
  );
}

function sortGroupedChanges(
  groups: GroupedProjectChange[],
): GroupedProjectChange[] {
  return [...groups].sort(
    (left, right) => getGroupPriority(left) - getGroupPriority(right),
  );
}

function hasImportantChanges(group: GroupedProjectChange): boolean {
  return group.changes.some((change) => change.important);
}

function getPrimaryGroupTarget(
  group: GroupedProjectChange,
): WorkspaceNotificationTarget | null {
  const primaryChange =
    group.changes.find((change) => isNavigableChange(change)) ?? null;
  return primaryChange ? buildNotificationTarget(primaryChange) : null;
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
  const { t } = useTranslation("dashboard");
  const tRef = useRef(t);
  tRef.current = t;
  const [, setLocation] = useLocation();
  const setCurrentProject = useProjectsStore((s) => s.setCurrentProject);
  const invalidateEntities = useAppStore((s) => s.invalidateEntities);
  const triggerScrollTo = useAppStore((s) => s.triggerScrollTo);
  const clearScrollTarget = useAppStore((s) => s.clearScrollTarget);
  const pushNotification = useAppStore((s) => s.pushNotification);
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
      setCurrentProject(projectName, res.project, res.scripts ?? {}, res.asset_fingerprints);
    } catch (err) {
      pushNotification(tRef.current("project_sync_failed", { message: errMsg(err) }), "warning");
    } finally {
      refreshingRef.current = false;
    }
    if (needsRefreshRef.current) {
      needsRefreshRef.current = false;
      void refreshProject();
      return;
    }
    flushQueuedFocus();
  }, [flushQueuedFocus, projectName, pushNotification, setCurrentProject]);

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

          // 提取并更新 asset fingerprints（零延迟，立即写入 store）
          const mergedFingerprints: Record<string, number> = {};
          for (const change of payload.changes) {
            if (change.asset_fingerprints) {
              Object.assign(mergedFingerprints, change.asset_fingerprints);
            }
          }
          if (Object.keys(mergedFingerprints).length > 0) {
            useProjectsStore.getState().updateAssetFingerprints(mergedFingerprints);
          }

          const invalidationKeys = payload.changes.map((change) =>
            buildEntityRevisionKey(change.entity_type, change.entity_id),
          );
          invalidateEntities(invalidationKeys);

          const groupedChanges = sortGroupedChanges(
            groupChangesByType(payload.changes),
          );

          if (payload.source !== "webui") {
            for (const group of groupedChanges) {
              if (!hasImportantChanges(group)) {
                continue;
              }
              pushNotification(formatGroupedNotificationText(group), "success");
            }
          }

          if (payload.source !== "webui") {
            // Draft 事件 — 自动导航到剧集预处理 Tab
            let draftHandled = false;
            for (const change of payload.changes) {
              if (
                change.entity_type === "draft" &&
                change.action === "created" &&
                typeof change.episode === "number" &&
                !isWorkspaceEditing()
              ) {
                startTransition(() => {
                  setLocation(`/episodes/${change.episode}`);
                });
                draftHandled = true;
                break;
              }
            }

            if (!draftHandled) {
              const nextFocusTarget =
                groupedChanges
                  .map((group) => {
                    const target = getPrimaryGroupTarget(group);
                    if (!target) {
                      return null;
                    }
                    pushWorkspaceNotification({
                      text: formatGroupedDeferredText(group),
                      target,
                    });
                    return target;
                  })
                  .find(Boolean) ?? null;

              queuedFocusRef.current = isWorkspaceEditing() ? null : nextFocusTarget;
            }
          }

          void refreshProject();

          // Refresh cost data when generation completes
          const hasGenerationEvent = payload.changes.some(
            (c) => c.action === "storyboard_ready" || c.action === "video_ready",
          );
          if (hasGenerationEvent && projectName) {
            useCostStore.getState().debouncedFetch(projectName);
          }

          // Refresh grid list when a grid completes
          if (payload.changes.some((c) => c.action === "grid_ready")) {
            useAppStore.getState().invalidateGrids();
          }
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
    invalidateEntities,
    projectName,
    pushNotification,
    pushWorkspaceNotification,
    refreshProject,
    setAssistantToolActivitySuppressed,
    setLocation,
  ]);
}
