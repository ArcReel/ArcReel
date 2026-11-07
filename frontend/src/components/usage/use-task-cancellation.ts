import { useCallback, useEffect, useRef, useState } from "react";

import { API, ApiRequestError } from "@/api";
import type { EpisodeItemRef } from "@/types";

interface CascadeTask {
  task_id: string;
  task_type: string;
  resource_id: string;
  resource_ref?: EpisodeItemRef | null;
}

/** Awaiting-confirmation cancellations; single tasks include cascade previews, Cancel all queued counts. */
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
  /** Confirmation-button pending state. */
  cancelling: boolean;
  /** Latest confirmed cancellation failed; retain confirmation for retries. */
  failed: boolean;
  /** Localized server rejection reasons (e.g. tasks already started); null for network failures. */
  failureDetail: string | null;
  /** Task IDs with pending cancellations, replacing inline × with spinners. */
  cancellingTaskIds: ReadonlySet<string>;
  requestSingle: (taskId: string) => Promise<void>;
  requestAll: (projectName: string) => Promise<void>;
  confirm: () => Promise<void>;
  dismiss: () => void;
}

/**
 * Two-step task cancellation: preview, then `alertdialog` confirmation. Queued tasks only
 * (ADR 0006); single previews include queued downstream cascades, while Cancel all clears queued tasks without touching running tasks.
 *
 * Preview errors after tasks leave cancellable states settle silently: next refreshes immediately replace rows,
 * and Cancel failed would imply missed clicks. Tasks starting at confirmation time receive server
 * 409 rejections; confirmations display server reasons.
 */
/** Confirmation-session states; gen increments on scope switches/closing, discarding old async results by gen. */
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
  // Scope switches create sessions: old-project confirmations disappear in the same render, and old pending results cannot write back.
  const [prevScopeKey, setPrevScopeKey] = useState(scopeKey);
  if (scopeKey !== prevScopeKey) {
    setPrevScopeKey(scopeKey);
    setSession((current) => idleSession(current.gen + 1));
  }
  const { gen, request, cancelling, failed, failureDetail } = session;
  const [cancellingTaskIds, setCancellingTaskIds] = useState<ReadonlySet<string>>(new Set());
  const previewAbort = useRef<AbortController | null>(null);

  // Write only while initiating sessions remain current.
  const updateSession = useCallback((forGen: number, patch: Partial<Omit<ConfirmSession, "gen">>) => {
    setSession((current) => (current.gen === forGen ? { ...current, ...patch } : current));
  }, []);

  // Invalidate pending previews from old scopes.
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
        // Tasks no longer cancellable, or previews superseded by newer operations.
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
        // No queued tasks, or previews superseded by newer operations.
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
        if (request.kind === "single") await API.cancelTask(request.taskId);
        else await API.cancelAllQueued(request.projectName);
      } catch (error) {
        // Cancellations not committed server-side retain failed confirmations, with visible causes/retry.
        updateSession(gen, {
          failed: true,
          failureDetail: error instanceof ApiRequestError && error.status === 409 ? error.message : null,
        });
        return;
      }
      // Committed cancellations close confirmations first; subsequent refetch failures are refresh problems, never cancellation failures.
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

  // Both buttons disable during confirmation requests; closing occurs only without pending confirmations.
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
