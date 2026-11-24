import { useTasksStore } from "@/stores/tasks-store";
import { useCallback, useEffect, useRef, useState } from "react";

import { API, ApiRequestError } from "@/api";
import type { EpisodeItemRef } from "@/types";

interface CascadeTask {
  task_id: string;
  task_type: string;
  resource_id: string;
  resource_ref?: EpisodeItemRef | null;
}

export type CancelRequest =
  | {
      kind: "single";
      taskId: string;
      cascaded: CascadeTask[];
    }
  | {
      kind: "all";
      projectName: string;
      queuedCount: number;
    };

export interface TaskCancellation {
  request: CancelRequest | null;
  cancelling: boolean;
  failed: boolean;
  failureDetail: string | null;
  cancellingTaskIds: ReadonlySet<string>;
  requestSingle: (taskId: string) => Promise<void>;
  requestAll: (projectName: string) => Promise<void>;
  confirm: () => Promise<void>;
  dismiss: () => void;
}

interface ConfirmSession {
  gen: number;
  request: CancelRequest | null;
  cancelling: boolean;
  failed: boolean;
  failureDetail: string | null;
}

const idleSession = (gen: number): ConfirmSession => ({
  gen,
  request: null,
  cancelling: false,
  failed: false,
  failureDetail: null,
});

export function useTaskCancellation(
  scopeKey: string | null,
  onCancelled?: () => void | Promise<void>,
): TaskCancellation {
  const [session, setSession] = useState(() => idleSession(0));
  const [prevScopeKey, setPrevScopeKey] = useState(scopeKey);
  if (scopeKey !== prevScopeKey) {
    setPrevScopeKey(scopeKey);
    setSession((current) => idleSession(current.gen + 1));
  }
  const { gen, request, cancelling, failed, failureDetail } = session;
  const [cancellingTaskIds, setCancellingTaskIds] = useState<ReadonlySet<string>>(new Set());
  const previewAbort = useRef<AbortController | null>(null);

  const updateSession = useCallback((forGen: number, patch: Partial<Omit<ConfirmSession, "gen">>) => {
    setSession((current) => (current.gen === forGen ? { ...current, ...patch } : current));
  }, []);

  useEffect(
    () => () => {
      previewAbort.current?.abort();
      previewAbort.current = null;
    },
    [scopeKey],
  );

  const beginPreview = useCallback(() => {
    previewAbort.current?.abort();
    const controller = new AbortController();
    previewAbort.current = controller;
    return controller;
  }, []);

  const requestSingle = useCallback(
    async (taskId: string) => {
      const controller = beginPreview();
      try {
        const preview = await API.cancelPreview(taskId, { signal: controller.signal });
        if (controller.signal.aborted) return;
        updateSession(gen, {
          request: { kind: "single", taskId, cascaded: preview.cascaded },
          failed: false,
          failureDetail: null,
        });
      } catch {
        // 任务已不在可取消状态，或预览被更新的操作接管
      }
    },
    [beginPreview, gen, updateSession],
  );

  const requestAll = useCallback(
    async (projectName: string) => {
      const controller = beginPreview();
      try {
        const { queued_count } = await API.cancelAllPreview(projectName, {
          signal: controller.signal,
        });
        if (controller.signal.aborted || queued_count === 0) return;
        updateSession(gen, {
          request: { kind: "all", projectName, queuedCount: queued_count },
          failed: false,
          failureDetail: null,
        });
      } catch {
        // 没有排队中的任务，或预览被更新的操作接管
      }
    },
    [beginPreview, gen, updateSession],
  );

  const confirm = useCallback(async () => {
    if (!request) return;
    updateSession(gen, { cancelling: true, failed: false, failureDetail: null });
    if (request.kind === "single") {
      const taskId = request.taskId;
      setCancellingTaskIds((prev) => new Set(prev).add(taskId));
    }
    try {
      try {
        if (request.kind === "single") await useTasksStore.getState().cancelTask(request.taskId);
        else await API.cancelAllQueued(request.projectName);
      } catch (error) {
        updateSession(gen, {
          failed: true,
          failureDetail: error instanceof ApiRequestError && error.status === 409 ? error.message : null,
        });
        return;
      }
      updateSession(gen, { request: null });
      await onCancelled?.();
    } finally {
      if (request.kind === "single") {
        const taskId = request.taskId;
        setCancellingTaskIds((prev) => {
          const next = new Set(prev);
          next.delete(taskId);
          return next;
        });
      }
      updateSession(gen, { cancelling: false });
    }
  }, [request, gen, updateSession, onCancelled]);

  const dismiss = useCallback(() => {
    setSession((current) => idleSession(current.gen + 1));
  }, []);

  return {
    request,
    cancelling,
    failed,
    failureDetail,
    cancellingTaskIds,
    requestSingle,
    requestAll,
    confirm,
    dismiss,
  };
}
