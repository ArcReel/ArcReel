import { startTransition, useCallback, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "wouter";
import { API } from "@/api";
import { useHasUnsavedChanges } from "@/components/shared/edit-unit/LeaveGuard";
import type { SseStreamHandle } from "@/utils/sse-stream";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore, type RefreshProjectResult } from "@/stores/projects-store";
import { useCostStore } from "@/stores/cost-store";
import { SCRIPT_PLAN_TASK_TYPES, useTasksStore } from "@/stores/tasks-store";
import { useUsageHeaderStore } from "@/stores/usage-header-store";
import { errMsg } from "@/utils/async";
import {
  WORKSPACE_ROUTE_CHARACTERS,
  WORKSPACE_ROUTE_EPISODES,
  WORKSPACE_ROUTE_PRODUCTS,
  WORKSPACE_ROUTE_PROPS,
  WORKSPACE_ROUTE_SCENES,
} from "@/app-routes";
import type {
  ProjectChange,
  ProjectChangeBatchPayload,
  ProjectChangeFocus,
  ProjectChangePane,
  WorkspaceNotificationTarget,
} from "@/types";
import {
  buildEntityRevisionKey,
  COMPLETION_ACTIONS,
  formatGroupedDeferredText,
  formatGroupedNotificationText,
  groupChangesByType,
  type GroupedProjectChange,
} from "@/utils/project-changes";

const REFERENCE_ASSET_ENTITY_TYPES: ReadonlySet<ProjectChange["entity_type"]> = new Set([
  "character",
  "scene",
  "prop",
  "product",
]);

const CHANGE_PRIORITY: Record<string, number> = {
  "segment:updated": 0,
  "drama_scene:updated": 0,
  "shot:updated": 0,
  "reference_unit:updated": 0,
  "character:created": 1,
  "character:updated": 2,
  "scene:created": 3,
  "scene:updated": 3.5,
  "prop:created": 4,
  "prop:updated": 4.5,
  "product:created": 4.6,
  "product:updated": 4.8,
  "episode:created": 5,
  "episode:updated": 6,
  "draft:created": 6.5,
  storyboard_ready: 7,
  video_ready: 8,
  grid_ready: 9,
  grid_split_done: 9.5,
  reference_video_ready: 10,
  tts_ready: 11,
  voice_sample_ready: 12,
};

function isTaskChange(change: ProjectChange): boolean {
  return change.entity_type === "task";
}

function isUsageRecordChange(change: ProjectChange): boolean {
  return change.entity_type === "usage_record";
}

function isTrashChange(change: ProjectChange): boolean {
  return change.entity_type === "trash";
}

function getChangePriority(change: ProjectChange): number {
  if (COMPLETION_ACTIONS.has(change.action)) {
    return CHANGE_PRIORITY[change.action] ?? Number.MAX_SAFE_INTEGER;
  }
  return CHANGE_PRIORITY[`${change.entity_type}:${change.action}`] ?? Number.MAX_SAFE_INTEGER;
}

function isNavigableChange(change: ProjectChange): boolean {
  if (COMPLETION_ACTIONS.has(change.action)) {
    return false;
  }
  return Boolean(change.focus?.anchor_type && change.focus?.anchor_id);
}

const PANE_ROUTES: Record<ProjectChangePane, (focus: ProjectChangeFocus) => string | null> = {
  characters: () => `/${WORKSPACE_ROUTE_CHARACTERS}`,
  scenes: () => `/${WORKSPACE_ROUTE_SCENES}`,
  props: () => `/${WORKSPACE_ROUTE_PROPS}`,
  products: () => `/${WORKSPACE_ROUTE_PRODUCTS}`,
  episode: (focus) =>
    typeof focus.episode === "number" ? `/${WORKSPACE_ROUTE_EPISODES}/${focus.episode}` : null,
};

const DRAFT_DOC_KINDS = ["script_plan", "prompt_authoring"] as const;

function buildNotificationTarget(change: ProjectChange): WorkspaceNotificationTarget | null {
  const focus = change.focus;
  if (!focus?.anchor_type || !focus.anchor_id) return null;

  const route = PANE_ROUTES[focus.pane]?.(focus);
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

function isWorkspaceEditing(hasUnsavedChanges: () => boolean): boolean {
  if (hasUnsavedChanges()) return true;
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
  useEffect(() => {
    tRef.current = t;
  }, [t]);
  const { t: tEvents } = useTranslation("events");
  const tEventsRef = useRef(tEvents);
  useEffect(() => {
    tEventsRef.current = tEvents;
  }, [tEvents]);
  const [, setLocation] = useLocation();
  const invalidateEntities = useAppStore((s) => s.invalidateEntities);
  const triggerScrollTo = useAppStore((s) => s.triggerScrollTo);
  const clearScrollTarget = useAppStore((s) => s.clearScrollTarget);
  const pushNotification = useAppStore((s) => s.pushNotification);
  const pushWorkspaceNotification = useAppStore((s) => s.pushWorkspaceNotification);
  const clearWorkspaceNotifications = useAppStore((s) => s.clearWorkspaceNotifications);
  const setAssistantToolActivitySuppressed = useAppStore(
    (s) => s.setAssistantToolActivitySuppressed
  );

  const hasUnsavedChanges = useHasUnsavedChanges();

  const sourceRef = useRef<SseStreamHandle | null>(null);
  const lastFingerprintRef = useRef<string | null>(null);
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
    if (isWorkspaceEditing(hasUnsavedChanges)) {
      return;
    }
    executeFocus(target);
  }, [executeFocus, hasUnsavedChanges]);

  const refreshProject = useCallback(async (): Promise<RefreshProjectResult> => {
    if (!projectName) return "cancelled";
    const focusSnapshot = queuedFocusRef.current;
    const result = await useProjectsStore.getState().refreshProject(projectName, {
      onError: (err) =>
        pushNotification(tRef.current("project_sync_failed", { message: errMsg(err) }), "warning"),
    });
    if (queuedFocusRef.current === focusSnapshot) {
      if (result === "success") {
        flushQueuedFocus();
      } else {
        queuedFocusRef.current = null;
      }
    }
    return result;
  }, [flushQueuedFocus, projectName, pushNotification]);

  useEffect(() => {
    lastFingerprintRef.current = null;
    queuedFocusRef.current = null;
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
          const episodes = useProjectsStore.getState().currentProjectData?.episodes ?? [];
          invalidateEntities(
            episodes.flatMap(({ episode }) =>
              DRAFT_DOC_KINDS.map((kind) => buildEntityRevisionKey("draft", `episode_${episode}_${kind}`)),
            ),
          );
          void refreshProject();
        }
        if (previousFingerprint) {
          void useUsageHeaderStore.getState().refresh();
          useAppStore.getState().invalidateTrash();
        }
        useAssistantStore.getState().requestSessionResync(projectName);
      },
      onChanges(payload: ProjectChangeBatchPayload) {
        if (disposed) return;
        lastFingerprintRef.current = payload.fingerprint;
        setAssistantToolActivitySuppressed(true);

        const taskChanges = payload.changes.filter(isTaskChange);
        const entityChanges = payload.changes.filter(
          (c) => !isTaskChange(c) && !isUsageRecordChange(c) && !isTrashChange(c),
        );
        if (payload.changes.some(isTrashChange)) {
          useAppStore.getState().invalidateTrash();
        }

        const mergedFingerprints: Record<string, number> = {};
        for (const change of entityChanges) {
          if (change.asset_fingerprints) {
            Object.assign(mergedFingerprints, change.asset_fingerprints);
          }
        }
        if (Object.keys(mergedFingerprints).length > 0) {
          useProjectsStore.getState().updateAssetFingerprints(mergedFingerprints);
        }

        const invalidationKeys = entityChanges.map((change) =>
          buildEntityRevisionKey(change.entity_type, change.entity_id),
        );
        if (taskChanges.some((c) => c.action === "task_succeeded" && SCRIPT_PLAN_TASK_TYPES.has(c.task_type ?? ""))) {
          const episodes = useProjectsStore.getState().currentProjectData?.episodes ?? [];
          invalidationKeys.push(
            ...episodes.map(({ episode }) => buildEntityRevisionKey("draft", `episode_${episode}_script_plan`)),
          );
        }
        if (
          taskChanges.some(
            (c) =>
              (c.action === "task_succeeded" || c.action === "task_failed" || c.action === "task_cancelled") &&
              c.task_type === "text_draft_repair",
          )
        ) {
          const episodes = useProjectsStore.getState().currentProjectData?.episodes ?? [];
          invalidationKeys.push(
            ...episodes.flatMap(({ episode }) =>
              DRAFT_DOC_KINDS.map((kind) => buildEntityRevisionKey("draft", `episode_${episode}_${kind}`)),
            ),
          );
        }
        if (invalidationKeys.length > 0) {
          invalidateEntities(invalidationKeys);
        }

        const groupedChanges = sortGroupedChanges(
          groupChangesByType(entityChanges),
        );

        const notifyChanges = entityChanges.length > 0 && payload.source !== "webui";
        const workspaceNotifications: { group: (typeof groupedChanges)[number]; target: WorkspaceNotificationTarget }[] =
          [];

        if (notifyChanges) {
          let draftHandled = false;
          for (const change of entityChanges) {
            if (
              change.entity_type === "draft" &&
              change.action === "created" &&
              typeof change.episode === "number" &&
              !isWorkspaceEditing(hasUnsavedChanges)
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
                  workspaceNotifications.push({ group, target });
                  return target;
                })
                .find(Boolean) ?? null;

            queuedFocusRef.current = isWorkspaceEditing(hasUnsavedChanges) ? null : nextFocusTarget;
          }
        }

        if (taskChanges.length > 0) {
          void useTasksStore.getState().refreshTasks();
        }

        if (
          entityChanges.some(
            (c) => c.entity_type === "reference_unit" || REFERENCE_ASSET_ENTITY_TYPES.has(c.entity_type),
          ) ||
          taskChanges.some(
            (c) => c.action === "task_succeeded" && c.task_type === "reference_video",
          )
        ) {
          useAppStore.getState().invalidateReferenceVideoUnits();
        }

        if (entityChanges.length > 0) {
          void refreshProject().then((result) => {
            if (disposed || !notifyChanges || result !== "success") return;
            const { currentProjectData, currentScripts } = useProjectsStore.getState();
            const episodeLedger = currentProjectData?.episodes ?? [];
            for (const group of groupedChanges) {
              if (!hasImportantChanges(group)) {
                continue;
              }
              pushNotification(
                formatGroupedNotificationText(group, tEventsRef.current, episodeLedger, currentScripts),
                "success",
              );
            }
            for (const { group, target } of workspaceNotifications) {
              pushWorkspaceNotification({
                text: formatGroupedDeferredText(group, tEventsRef.current, episodeLedger, currentScripts),
                target,
              });
            }
          });
        }

        const hasCompletionEvent = entityChanges.some((c) =>
          COMPLETION_ACTIONS.has(c.action),
        );
        const hasBilledVoiceSampleTerminal = taskChanges.some(
          (c) =>
            (c.action === "task_failed" || c.action === "task_cancelled") &&
            c.task_type === "voice_sample",
        );
        const hasGridSplit = entityChanges.some((c) => c.action === "grid_split_done");
        const hasUsageRecord = payload.changes.some(isUsageRecordChange);
        if (hasUsageRecord || taskChanges.length > 0) {
          void useUsageHeaderStore.getState().refresh();
        }
        if (
          (hasCompletionEvent || hasBilledVoiceSampleTerminal || hasGridSplit || hasUsageRecord) &&
          projectName
        ) {
          useCostStore.getState().debouncedFetch(projectName);
        }

        if (entityChanges.some((c) => c.action === "grid_ready" || c.action === "grid_split_done")) {
          useAppStore.getState().invalidateGrids();
        }
      },
      onAssistantSessionResumed(payload) {
        if (disposed) return;
        useAssistantStore.getState().notifySessionResumed(payload.project_name, payload.session_id);
      },
      onProjectDeleted() {
        if (disposed) return;
        if (sourceRef.current) {
          sourceRef.current.close();
          sourceRef.current = null;
        }
      },
    });
    sourceRef.current = source;

    return () => {
      disposed = true;
      if (sourceRef.current) {
        sourceRef.current.close();
        sourceRef.current = null;
      }
    };
  }, [
    clearWorkspaceNotifications,
    hasUnsavedChanges,
    invalidateEntities,
    projectName,
    pushNotification,
    pushWorkspaceNotification,
    refreshProject,
    setAssistantToolActivitySuppressed,
    setLocation,
  ]);
}
