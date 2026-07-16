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
  COMPLETION_ACTIONS,
  formatGroupedDeferredText,
  formatGroupedNotificationText,
  GENERATION_ACTIONS,
  groupChangesByType,
  type GroupedProjectChange,
} from "@/utils/project-changes";

const CHANGE_PRIORITY: Record<string, number> = {
  "segment:updated": 0,
  // drama/ad/参考生视频骨架条目的 updated 与 narration 分镜同优先级，四种骨架通知排序一致。
  "drama_scene:updated": 0,
  "shot:updated": 0,
  "reference_unit:updated": 0,
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
  reference_video_ready: 10,
  tts_ready: 11,
};

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
  // 把 t 通过 ref 暴露给 callback，避免 i18n 切语言时 refreshProject
  // 重建 → EventSource effect 跟着重连 → 通知/focus 提示丢失。
  const tRef = useRef(t);
  useEffect(() => {
    tRef.current = t;
  }, [t]);
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

  const sourceRef = useRef<EventSource | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastFingerprintRef = useRef<string | null>(null);
  const queuedFocusRef = useRef<WorkspaceNotificationTarget | null>(null);
  // 项目已被删除（收到终止事件）：停止断线重连循环，不再对已删项目周期性发起请求。
  const terminatedRef = useRef(false);

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
    // 在途合并逻辑（单飞 + 排队再跑一轮 + 失败留旧）已下沉到 projects-store.refreshProject；
    // 此处只保留 SSE 专属包装：失败时告警、刷新落定后消费排队的聚焦目标。
    //
    // refreshProject 现在按轮次各自 resolve（见 projects-store），因此本次调用落定时，
    // 可能已有更晚一批 onChanges 把 queuedFocusRef 改写为它自己的目标——那个新目标
    // 对应的数据要等它自己那一轮 getProject 完成才会写入 store。无条件消费 ref 会拿着
    // 尚未落库的目标提前导航/滚动，且消费后 ref 被清空，那一批之后也不会再重试；这个
    // 风险不局限于「本次调用自己设置了新目标」的场景——不设置新目标的调用（onSnapshot、
    // webui 来源、draftHandled 分支）同样可能在等待落定期间被别的调用改写 ref。
    //
    // 因此改为在发起请求前对 ref 拍快照，落定后只在 ref 仍等于快照时才消费——说明这段
    // 等待期间没有别的调用改写过它，可以放心视为「跟自己这一轮对应」；ref 已变则跳过，
    // 交由改写它的那次调用在自己对应轮次落定后消费。
    const focusSnapshot = queuedFocusRef.current;
    await useProjectsStore.getState().refreshProject(projectName, {
      onError: (err) =>
        pushNotification(tRef.current("project_sync_failed", { message: errMsg(err) }), "warning"),
    });
    if (queuedFocusRef.current !== focusSnapshot) {
      return;
    }
    flushQueuedFocus();
  }, [flushQueuedFocus, projectName, pushNotification]);

  useEffect(() => {
    lastFingerprintRef.current = null;
    queuedFocusRef.current = null;
    terminatedRef.current = false;
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
          const hasGenerationEvent = payload.changes.some((c) =>
            GENERATION_ACTIONS.has(c.action),
          );
          if (hasGenerationEvent && projectName) {
            useCostStore.getState().debouncedFetch(projectName);
          }

          // Refresh grid list when a grid completes
          if (payload.changes.some((c) => c.action === "grid_ready")) {
            useAppStore.getState().invalidateGrids();
          }
        },
        onProjectDeleted() {
          if (disposed) return;
          // 项目目录已被删除：后端已正常关流，停止重连循环——不对已删项目周期性发起请求。
          // 浏览器随后会因连接结束触发一次 onError；terminatedRef 拦住它排的重连。
          terminatedRef.current = true;
          if (reconnectTimerRef.current) {
            clearTimeout(reconnectTimerRef.current);
            reconnectTimerRef.current = null;
          }
          if (sourceRef.current) {
            sourceRef.current.close();
            sourceRef.current = null;
          }
        },
        onError() {
          if (disposed) return;
          if (terminatedRef.current) return;
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
