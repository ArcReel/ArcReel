import { startTransition, useCallback, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "wouter";
import { API } from "@/api";
import { useHasUnsavedChanges } from "@/components/shared/edit-unit/LeaveGuard";
import type { SseStreamHandle } from "@/utils/sse-stream";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useItemLocationsStore } from "@/stores/item-locations-store";
import {
  useProjectsStore,
  type ProjectRefreshScope,
  type RefreshProjectResult,
} from "@/stores/projects-store";
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
  ItemLocation,
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

const SCRIPT_PLAN_DEPENDENCY_ENTITY_TYPES: ReadonlySet<ProjectChange["entity_type"]> = new Set([
  "character",
  "scene",
  "prop",
  "product",
  "project",
]);

const EPISODE_SCOPED_ENTITY_TYPES: ReadonlySet<ProjectChange["entity_type"]> = new Set([
  "segment",
  "drama_scene",
  "shot",
  "reference_unit",
  "script_plan",
  "draft",
  "grid",
]);

const SCRIPT_ITEM_ENTITY_TYPES: ReadonlySet<ProjectChange["entity_type"]> = new Set([
  "segment",
  "drama_scene",
  "shot",
  "reference_unit",
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

function isDocumentHidden(): boolean {
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

function deriveRefreshScope(entityChanges: readonly ProjectChange[]): ProjectRefreshScope {
  const episodes = new Set<number>();
  let assets = false;
  let projectWide = false;

  for (const change of entityChanges) {
    if (REFERENCE_ASSET_ENTITY_TYPES.has(change.entity_type)) {
      assets = true;
      projectWide = true;
      continue;
    }
    if (change.entity_type === "episode") {
      if (change.action === "updated" && typeof change.episode === "number") {
        episodes.add(change.episode);
      } else {
        projectWide = true;
      }
      continue;
    }
    if (EPISODE_SCOPED_ENTITY_TYPES.has(change.entity_type) && typeof change.episode === "number") {
      episodes.add(change.episode);
      continue;
    }
    projectWide = true;
  }

  if (projectWide) {
    return { projectWide: true, assets };
  }
  return {
    episodes: [...episodes],
    assets: false,
    projectWide: false,
  };
}

function mergePendingScope(
  current: ProjectRefreshScope | null,
  incoming: ProjectRefreshScope,
): ProjectRefreshScope {
  if (!current) return incoming;
  if (current.projectWide || incoming.projectWide) {
    return { projectWide: true, assets: Boolean(current.assets || incoming.assets) };
  }
  const episodes = new Set([...(current.episodes ?? []), ...(incoming.episodes ?? [])]);
  return {
    episodes: [...episodes],
    assets: Boolean(current.assets || incoming.assets),
    projectWide: false,
  };
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

interface PendingHiddenSync {
  notifications: Array<() => void>;
  workspace: boolean;
  scope: ProjectRefreshScope | null;
  tasks: boolean;
  usage: boolean;
  cost: boolean;
  trash: boolean;
  referenceUnits: boolean;
  grids: boolean;
  snapshotReady: boolean;
  invalidationKeys: Set<string>;
}

function emptyHiddenSync(): PendingHiddenSync {
  return {
    notifications: [],
    workspace: false,
    scope: null,
    tasks: false,
    usage: false,
    cost: false,
    trash: false,
    referenceUnits: false,
    grids: false,
    snapshotReady: false,
    invalidationKeys: new Set(),
  };
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
    (s) => s.setAssistantToolActivitySuppressed,
  );

  const hasUnsavedChanges = useHasUnsavedChanges();

  const sourceRef = useRef<SseStreamHandle | null>(null);
  const lastFingerprintRef = useRef<string | null>(null);
  const queuedFocusRef = useRef<WorkspaceNotificationTarget | null>(null);
  const hiddenSyncRef = useRef<PendingHiddenSync | null>(null);

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

  const refreshProject = useCallback(
    async (scope: ProjectRefreshScope): Promise<RefreshProjectResult> => {
      if (!projectName) return "cancelled";
      const focusSnapshot = queuedFocusRef.current;
      let scriptCancelledAfterCommit = false;
      const pending = hiddenSyncRef.current;
      const retryWorkspace = pending?.workspace;
      const notifications = retryWorkspace ? pending.notifications.splice(0) : [];
      if (pending && retryWorkspace) {
        scope = mergePendingScope(pending.scope, scope);
        pending.scope = null;
        pending.workspace = false;
      }
      const result = await useProjectsStore.getState().refreshProject(projectName, {
        onScriptCancelledAfterCommit: () => { scriptCancelledAfterCommit = true; },
        scope,
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
      if (pending && retryWorkspace && hiddenSyncRef.current === pending) {
        if (result === "success" || scriptCancelledAfterCommit) {
          notifications.forEach((notify) => notify());
        } else {
          pending.scope = mergePendingScope(pending.scope, scope);
          pending.workspace = true;
          pending.notifications.unshift(...notifications);
        }
      }
      return scriptCancelledAfterCommit ? "success" : result;
    },
    [flushQueuedFocus, projectName, pushNotification],
  );

  useEffect(() => {
    lastFingerprintRef.current = null;
    queuedFocusRef.current = null;
    hiddenSyncRef.current = emptyHiddenSync();
    clearScrollTarget();
    clearWorkspaceNotifications();
    return () => {
      queuedFocusRef.current = null;
      hiddenSyncRef.current = emptyHiddenSync();
      clearScrollTarget();
      clearWorkspaceNotifications();
    };
  }, [clearScrollTarget, clearWorkspaceNotifications, projectName]);

  useEffect(() => {
    if (!projectName || typeof document === "undefined") return;
    const onVisibilityChange = () => {
      if (document.visibilityState !== "visible") return;
      const pending = hiddenSyncRef.current ??= emptyHiddenSync();
      useItemLocationsStore.getState().resumePending(projectName);
      const hasPending =
        pending.workspace ||
        pending.tasks ||
        pending.usage ||
        pending.cost ||
        pending.trash ||
        pending.referenceUnits ||
        pending.grids ||
        pending.snapshotReady ||
        pending.invalidationKeys.size > 0;
      if (!hasPending) return;
      const deferred = { ...pending };
      if (pending.workspace) void refreshProject(pending.scope ?? { projectWide: true, assets: true });
      Object.assign(pending, emptyHiddenSync());
      if (deferred.invalidationKeys.size > 0) {
        invalidateEntities([...deferred.invalidationKeys]);
      }
      if (deferred.trash) useAppStore.getState().invalidateTrash();
      if (deferred.referenceUnits) useAppStore.getState().invalidateReferenceVideoUnits();
      if (deferred.grids) useAppStore.getState().invalidateGrids();
      if (deferred.snapshotReady) {
        useItemLocationsStore.getState().notifySnapshotReady(projectName, false);
      }
      if (deferred.tasks) void useTasksStore.getState().refreshTasks();
      if (deferred.usage) void useUsageHeaderStore.getState().refresh();
      if (deferred.cost) useCostStore.getState().debouncedFetch(projectName);
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => document.removeEventListener("visibilitychange", onVisibilityChange);
  }, [invalidateEntities, projectName, refreshProject]);

  useEffect(() => {
    if (!projectName) return;
    let disposed = false;
    if (sourceRef.current) {
      sourceRef.current.close();
      sourceRef.current = null;
    }

    const source = API.openProjectEventStream({
      projectName,
      activeEpisode: () => {
        const active = useProjectsStore.getState().activeEpisodeScript;
        return active.projectName === projectName ? active.episode : null;
      },
      onSnapshot(payload) {
        if (disposed) return;
        useProjectsStore.getState().receiveScriptBaseline(projectName, payload.episode_script);
        const previousFingerprint = lastFingerprintRef.current;
        lastFingerprintRef.current = payload.fingerprint;
        const fingerprintChanged = Boolean(previousFingerprint && previousFingerprint !== payload.fingerprint);
        if (isDocumentHidden()) {
          const pending = hiddenSyncRef.current ??= emptyHiddenSync();
          pending.snapshotReady = true;
          if (fingerprintChanged) {
            useItemLocationsStore.getState().invalidateEpisodes(projectName);
            const episodes = useProjectsStore.getState().currentProjectData?.episodes ?? [];
            for (const { episode } of episodes) {
              pending.invalidationKeys.add(buildEntityRevisionKey("script_plan", `episode_${episode}`));
            }
            pending.workspace = true;
            pending.scope = mergePendingScope(pending.scope, { projectWide: true, assets: true });
          }
          if (previousFingerprint) {
            pending.tasks = true;
            pending.usage = true;
            pending.trash = true;
          }
          useAssistantStore.getState().requestSessionResync(projectName);
          return;
        }

        useItemLocationsStore.getState().notifySnapshotReady(projectName, fingerprintChanged);
        if (fingerprintChanged) {
          const episodes = useProjectsStore.getState().currentProjectData?.episodes ?? [];
          invalidateEntities(
            episodes.map(({ episode }) => buildEntityRevisionKey("script_plan", `episode_${episode}`)),
          );
          void refreshProject({ projectWide: true, assets: true });
        } else if (hiddenSyncRef.current?.scope) {
          void refreshProject(hiddenSyncRef.current.scope);
        }
        if (previousFingerprint) {
          void useTasksStore.getState().refreshTasks();
          void useUsageHeaderStore.getState().refresh();
          useAppStore.getState().invalidateTrash();
        }
        useAssistantStore.getState().requestSessionResync(projectName);
      },
      onTaskProgress(progress) {
        if (!disposed) useTasksStore.getState().receiveProgress(progress);
      },
      onChanges(payload: ProjectChangeBatchPayload) {
        if (disposed) return;
        lastFingerprintRef.current = payload.fingerprint;
        setAssistantToolActivitySuppressed(true);

        const taskChanges = payload.changes.filter(isTaskChange);
        const entityChanges = payload.changes.filter(
          (c) => !isTaskChange(c) && !isUsageRecordChange(c) && !isTrashChange(c),
        );
        const hasTrash = payload.changes.some(isTrashChange);

        const mergedFingerprints: Record<string, number> = {};
        const directLocations: ItemLocation[] = [];
        const structurallyChangedEpisodes = new Set<number>();
        for (const change of entityChanges) {
          if (change.asset_fingerprints) {
            Object.assign(mergedFingerprints, change.asset_fingerprints);
          }
          if (change.entity_type === "episode" && typeof change.episode === "number") {
            structurallyChangedEpisodes.add(change.episode);
          }
          if (SCRIPT_ITEM_ENTITY_TYPES.has(change.entity_type)) {
            if (change.position !== undefined) {
              directLocations.push({
                episode: change.episode ?? null,
                item_id: change.item_id ?? change.entity_id,
                position: change.position,
                status: change.location_status ?? (change.position !== null ? "found" : change.action === "deleted" ? "deleted" : "unloaded"),
              });
            }
            if ((change.action === "created" || change.action === "deleted") && typeof change.episode === "number") {
              structurallyChangedEpisodes.add(change.episode);
            }
          }
        }
        if (Object.keys(mergedFingerprints).length > 0) {
          useProjectsStore.getState().updateAssetFingerprints(mergedFingerprints);
        }
        if (structurallyChangedEpisodes.size > 0) {
          useItemLocationsStore.getState().invalidateEpisodes(projectName, [...structurallyChangedEpisodes]);
        }
        if (directLocations.length > 0) {
          useItemLocationsStore.getState().recordLocations(projectName, directLocations);
        }

        const invalidationKeys = entityChanges.map((change) =>
          buildEntityRevisionKey(change.entity_type, change.entity_id),
        );
        if (entityChanges.some((c) => SCRIPT_PLAN_DEPENDENCY_ENTITY_TYPES.has(c.entity_type))) {
          const episodes = useProjectsStore.getState().currentProjectData?.episodes ?? [];
          invalidationKeys.push(
            ...episodes.map(({ episode }) => buildEntityRevisionKey("script_plan", `episode_${episode}`)),
          );
        }
        for (const change of entityChanges) {
          if (change.entity_type === "episode" && typeof change.episode === "number") {
            invalidationKeys.push(buildEntityRevisionKey("script_plan", `episode_${change.episode}`));
          }
        }
        const scriptPlanSuccessChanges = taskChanges.filter(
          (c) => c.action === "task_succeeded" && SCRIPT_PLAN_TASK_TYPES.has(c.task_type ?? ""),
        );
        if (scriptPlanSuccessChanges.length > 0) {
          const explicitEpisodes = scriptPlanSuccessChanges
            .map((c) => c.episode)
            .filter((ep): ep is number => typeof ep === "number");
          if (explicitEpisodes.length === scriptPlanSuccessChanges.length) {
            for (const ep of explicitEpisodes) {
              invalidationKeys.push(buildEntityRevisionKey("script_plan", `episode_${ep}`));
            }
          } else {
            const episodes = useProjectsStore.getState().currentProjectData?.episodes ?? [];
            invalidationKeys.push(
              ...episodes.map(({ episode }) => buildEntityRevisionKey("script_plan", `episode_${episode}`)),
            );
          }
        }

        const hasReferenceUnitChange =
          entityChanges.some(
            (c) => c.entity_type === "reference_unit" || REFERENCE_ASSET_ENTITY_TYPES.has(c.entity_type),
          ) ||
          taskChanges.some(
            (c) =>
              c.action === "task_succeeded" &&
              (c.task_type === "reference_video" || c.task_type === "text_episode_script"),
          );
        const hasCompletionEvent = entityChanges.some((c) => COMPLETION_ACTIONS.has(c.action));
        const hasBilledVoiceSampleTerminal = taskChanges.some(
          (c) =>
            (c.action === "task_failed" || c.action === "task_cancelled") &&
            c.task_type === "voice_sample",
        );
        const hasGridSplit = entityChanges.some((c) => c.action === "grid_split_done");
        const hasGridEvent = entityChanges.some(
          (c) => c.action === "grid_ready" || c.action === "grid_split_done",
        );
        const hasUsageRecord = payload.changes.some(isUsageRecordChange);
        const hasCostChange =
          hasCompletionEvent || hasBilledVoiceSampleTerminal || hasGridSplit || hasUsageRecord;
        const refreshScope = entityChanges.length > 0 ? deriveRefreshScope(entityChanges) : null;

        const groupedChanges = sortGroupedChanges(groupChangesByType(entityChanges));

        const notifyChanges = entityChanges.length > 0 && payload.source !== "webui";
        const workspaceNotifications: { group: (typeof groupedChanges)[number]; target: WorkspaceNotificationTarget }[] =
          [];

        if (notifyChanges) {
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

          if (!isDocumentHidden()) queuedFocusRef.current = isWorkspaceEditing(hasUnsavedChanges) ? null : nextFocusTarget;
        }

        const deliverNotifications = () => {
          if (disposed) return;
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
        };

        if (isDocumentHidden()) {
          const pending = hiddenSyncRef.current ??= emptyHiddenSync();
          for (const key of invalidationKeys) pending.invalidationKeys.add(key);
          if (hasTrash) pending.trash = true;
          if (taskChanges.length > 0) pending.tasks = true;
          if (hasReferenceUnitChange) pending.referenceUnits = true;
          if (hasGridEvent) pending.grids = true;
          if (hasUsageRecord || taskChanges.length > 0) pending.usage = true;
          if (hasCostChange) pending.cost = true;
          if (refreshScope) {
            pending.workspace = true;
            if (notifyChanges) pending.notifications.push(deliverNotifications);
            pending.scope = mergePendingScope(pending.scope, refreshScope);
          }
          return;
        }

        if (hasTrash) {
          useAppStore.getState().invalidateTrash();
        }
        if (invalidationKeys.length > 0) {
          invalidateEntities(invalidationKeys);
        }

        if (taskChanges.length > 0) {
          void useTasksStore.getState().refreshTasks();
        }

        if (hasReferenceUnitChange) {
          useAppStore.getState().invalidateReferenceVideoUnits();
        }

        if (refreshScope) {
          void refreshProject(refreshScope).then((result) => {
            if (disposed || !notifyChanges || result !== "success") return;
            deliverNotifications();
          });
        }

        if (hasUsageRecord || taskChanges.length > 0) {
          void useUsageHeaderStore.getState().refresh();
        }
        if (hasCostChange && projectName) {
          useCostStore.getState().debouncedFetch(projectName);
        }
        if (hasGridEvent) {
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
