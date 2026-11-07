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


/** Asset entities usable as video-unit references: additions/changes/removals alter available references and bucket selection. */
const REFERENCE_ASSET_ENTITY_TYPES: ReadonlySet<ProjectChange["entity_type"]> = new Set([
  "character",
  "scene",
  "prop",
  "product",
]);

const CHANGE_PRIORITY: Record<string, number> = {
  "segment:updated": 0,
  // drama/ad/reference-video skeleton-entry updates share narration-storyboard priority, aligning all four skeleton notification orders.
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

/** Task terminal-state changes (refresh signals, never project entity changes). */
function isTaskChange(change: ProjectChange): boolean {
  return change.entity_type === "task";
}

/** Accounting settlement changes (refresh signals, never project entity changes). */
function isUsageRecordChange(change: ProjectChange): boolean {
  return change.entity_type === "usage_record";
}

/** Project-trash changes (refresh signals, never project entity changes). */
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

/** Exhaustive pane-union → workspace routes; null means missing routing information, not navigable. */
const PANE_ROUTES: Record<ProjectChangePane, (focus: ProjectChangeFocus) => string | null> = {
  characters: () => `/${WORKSPACE_ROUTE_CHARACTERS}`,
  scenes: () => `/${WORKSPACE_ROUTE_SCENES}`,
  props: () => `/${WORKSPACE_ROUTE_PROPS}`,
  products: () => `/${WORKSPACE_ROUTE_PRODUCTS}`,
  episode: (focus) =>
    typeof focus.episode === "number" ? `/${WORKSPACE_ROUTE_EPISODES}/${focus.episode}` : null,
};

/** Draft-view entity-ID suffixes: `draft:episode_N_<kind>`. */
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

/**
 * Never automatically focus/navigate Agent edits while users edit: focused inputs, legacy editing cards,
 * or unsaved edit units such as storyboard/unit bodies, whose departure guards cannot rely on focus alone.
 */
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
  // Expose latest t via refs to avoid language changes rebuilding refreshProject,
  // reconnecting stream effects and losing notifications/focus notices.
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
    // Merge coordination (single flight/queued follow-up/retain on failure) lives in projects-store.refreshProject;
    // retain SSE-only wrappers: failure notices and settled-refresh queued focus consumption.
    //
    // refreshProject resolves per round (projects-store); by settlement,
    // newer onChanges may have replaced queuedFocusRef with their own targets, whose data
    // waits for their own getProject rounds. Unconditional consumption navigates/scrolls
    // before target persistence, then clears refs so later batches never retry. This risk
    // also affects calls setting no targets: onSnapshot, webui sources and draftHandled
    // branches can all have refs replaced during waits.
    //
    // Snapshot refs before requests, consuming only if still identical after settlement:
    // no intervening writes means safe ownership by this round. Changed refs yield
    // consumption to their own writing calls after corresponding rounds settle.
    const focusSnapshot = queuedFocusRef.current;
    const result = await useProjectsStore.getState().refreshProject(projectName, {
      onError: (err) =>
        pushNotification(tRef.current("project_sync_failed", { message: errMsg(err) }), "warning"),
    });
    if (queuedFocusRef.current === focusSnapshot) {
      flushQueuedFocus();
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

    // Streaming clients own reconnections; resent snapshots refresh projects only on changed fingerprints.
    const source = API.openProjectEventStream({
      projectName,
      onSnapshot(payload) {
        if (disposed) return;
        const previousFingerprint = lastFingerprintRef.current;
        lastFingerprintRef.current = payload.fingerprint;
        if (previousFingerprint && previousFingerprint !== payload.fingerprint) {
          // Draft views fetch independently on draft events; invalidate missed disconnected-period changes here too.
          const episodes = useProjectsStore.getState().currentProjectData?.episodes ?? [];
          invalidateEntities(
            episodes.flatMap(({ episode }) =>
              DRAFT_DOC_KINDS.map((kind) => buildEntityRevisionKey("draft", `episode_${episode}_${kind}`)),
            ),
          );
          void refreshProject();
        }
        // Snapshots arrive on every connection; all after initial imply disconnects. Missed settlements/
        // task terminals require full header-usage refetch, since usage otherwise refreshes by events.
        if (previousFingerprint) {
          void useUsageHeaderStore.getState().refresh();
          // Trash is absent from snapshot fingerprints; disconnected changes also require refetch here.
          useAppStore.getState().invalidateTrash();
        }
        // Recovery notices emit once, potentially before subscriptions/initial connection retries;
        // reconcile initial snapshots too, since session-hook loads may predate recovery.
        useAssistantStore.getState().requestSessionResync(projectName);
      },
      onChanges(payload: ProjectChangeBatchPayload) {
        if (disposed) return;
        lastFingerprintRef.current = payload.fingerprint;
        setAssistantToolActivitySuppressed(true);

        // Task terminals are refresh signals, not entity changes; separate before invalidation/grouping/
        // focus handling. Mixing causes two concrete failures: terminal tasks leave
        // unused `task:<uuid>` in `entityRevisions`, never cleared on project switches,
        // with full copies per invalidation and unbounded long-session growth. Targetless task batches
        // also clear prior queued entity `queuedFocusRef` waiting on refreshProject,
        // losing expected navigation/highlights.
        const taskChanges = payload.changes.filter(isTaskChange);
        // Settlements are refresh signals too; mixing leaves unused entityRevisions
        // `usage_record:<id>` keys and clears prior entity focus targets.
        const entityChanges = payload.changes.filter(
          (c) => !isTaskChange(c) && !isUsageRecordChange(c) && !isTrashChange(c),
        );
        if (payload.changes.some(isTrashChange)) {
          useAppStore.getState().invalidateTrash();
        }

        // Extract/update asset fingerprints immediately into stores.
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
        // Formal script plans are outside snapshots; replanning completion has no entity changes. Terminal tasks invalidate
        // every episode plan view, refetching mounted confirmation views only because events lack episode IDs.
        if (taskChanges.some((c) => c.action === "task_succeeded" && SCRIPT_PLAN_TASK_TYPES.has(c.task_type ?? ""))) {
          const episodes = useProjectsStore.getState().currentProjectData?.episodes ?? [];
          invalidationKeys.push(
            ...episodes.map(({ episode }) => buildEntityRevisionKey("draft", `episode_${episode}_script_plan`)),
          );
        }
        // Worker AI repairs write/adopt drafts without entity changes; terminal events likewise invalidate episode draft views.
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
          // Draft events automatically navigate episode script-planning tabs.
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

        // Task terminals immediately refetch tasks/statistics without polling delays.
        if (taskChanges.length > 0) {
          void useTasksStore.getState().refreshTasks();
        }

        // Unit changes from Agents/other browsers, finished generation and asset changes alter
        // available references and server-derived per-unit buckets/tiers; all three invalidate
        // independent reference-video-store caches once per batch.
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

        // Refetch projects only for entity changes; task terminals/settlements do not alter snapshots.
        // Backend rescans/broadcasts reconnect-window changes absent from in-flight batches separately.
        // Notification titles/positions use refreshed ledgers: Agent-created/renamed/reordered episodes
        // are absent/stale locally on arrival; old-ledger notices irreversibly show unnamed/old titles.
        // Failed refreshes emit no notices: new entries absent from old scripts appear Deleted; sync failures already notify.
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

        // Refresh cost data when generation completes
        const hasCompletionEvent = entityChanges.some((c) =>
          COMPLETION_ACTIONS.has(c.action),
        );
        // voice_sample billing occurs after synthesis success, before duration/size checks or cancellation:
        // invalid outputs fail and midexecution cancellations settle cancelled, neither emitting
        // voice_sample_ready, sent only through emit_generation_success_batch when executors return
        // successfully. Costs already occurred, so nonsuccess terminals must also refresh costs,
        // avoiding real charges with stale cost panels.
        const hasBilledVoiceSampleTerminal = taskChanges.some(
          (c) =>
            (c.action === "task_failed" || c.action === "task_cancelled") &&
            c.task_type === "voice_sample",
        );
        // Splitting itself costs nothing but assigns generated_assets.grid_id, moving incurred grid
        // costs from unattributed to episode allocation (ADR 0053). Refetch changed ownership or panels
        // retain presplit allocation. grid_split_done is outside COMPLETION_ACTIONS, which classifies
        // generation-completion notices, so handle separately.
        const hasGridSplit = entityChanges.some((c) => c.action === "grid_split_done");
        // Persisted provider-call settlement (success/failure/cancel) means cost changes; taskless text
        // and assistant sessions rely solely on this signal, lacking task terminals/completion notices.
        const hasUsageRecord = payload.changes.some(isUsageRecordChange);
        // Header usage uses two signals: settlements update money/completed lists, terminals update ongoing lists.
        if (hasUsageRecord || taskChanges.length > 0) {
          void useUsageHeaderStore.getState().refresh();
        }
        if (
          (hasCompletionEvent || hasBilledVoiceSampleTerminal || hasGridSplit || hasUsageRecord) &&
          projectName
        ) {
          useCostStore.getState().debouncedFetch(projectName);
        }

        // Refresh grid list when a grid completes or gets split into cells
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
        // Deleted project directories close server streams normally; close handles to stop periodically requesting absent projects.
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
