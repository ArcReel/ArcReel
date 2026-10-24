import { useCallback, useEffect, useRef, useState } from "react";

import { API, ApiRequestError } from "@/api";
import type { EpisodeItemRef } from "@/types";

interface CascadeTask {
  task_id: string;
  task_type: string;
  resource_id: string;
  resource_ref?: EpisodeItemRef | null;
}

/** 待确认的取消请求。单个取消带级联预览，全部取消带排队中条数。 */
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
  /** 确认按钮的在途状态。 */
  cancelling: boolean;
  /** 上一次确认的取消请求失败；确认态保留，用户可重试。 */
  failed: boolean;
  /** 服务端拒绝取消时给出的已本地化原因（如任务已开始执行）；网络类失败为 null。 */
  failureDetail: string | null;
  /** 取消请求在途的任务 id；行内把 × 换成 spinner 用它判定。 */
  cancellingTaskIds: ReadonlySet<string>;
  requestSingle: (taskId: string) => Promise<void>;
  requestAll: (projectName: string) => Promise<void>;
  confirm: () => Promise<void>;
  dismiss: () => void;
}

/**
 * 取消任务的两步交互：先取预览、再由 `alertdialog` 二次确认。取消只对排队中的任务开放
 * （ADR 0006）——单个取消预览会被级联取消的排队中下游，全部取消只清排队中、不动执行中。
 *
 * 预览接口在任务已离开可取消状态时报错，此时静默收场：这一行马上会被下一轮任务刷新
 * 改写，弹一个「取消失败」反而让用户以为自己漏点了。确认时任务恰好已开始执行，服务端
 * 以 409 拒绝，确认态展示服务端给出的原因。
 */
/** 一次确认会话的状态。gen 在切换作用域或关闭确认时递增，旧会话的异步结果按 gen 丢弃。 */
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
  // 切换作用域即开启新会话：旧项目的确认态在同一次渲染里消失，旧会话在途请求的结果不再写回
  const [prevScopeKey, setPrevScopeKey] = useState(scopeKey);
  if (scopeKey !== prevScopeKey) {
    setPrevScopeKey(scopeKey);
    setSession((current) => idleSession(current.gen + 1));
  }
  const { gen, request, cancelling, failed, failureDetail } = session;
  const [cancellingTaskIds, setCancellingTaskIds] = useState<ReadonlySet<string>>(new Set());
  const previewAbort = useRef<AbortController | null>(null);

  // 只在发起时的会话仍是当前会话时写入
  const updateSession = useCallback((forGen: number, patch: Partial<Omit<ConfirmSession, "gen">>) => {
    setSession((current) => (current.gen === forGen ? { ...current, ...patch } : current));
  }, []);

  // 作废旧作用域在途的预览请求
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
        if (request.kind === "single") await API.cancelTask(request.taskId);
        else await API.cancelAllQueued(request.projectName);
      } catch (error) {
        // 取消没有落到服务端：确认态留在原地并标记失败，用户看得到原因、也能重试。
        updateSession(gen, {
          failed: true,
          failureDetail: error instanceof ApiRequestError && error.status === 409 ? error.message : null,
        });
        return;
      }
      // 取消已落地，确认态先收起；随后的重取失败是刷新问题，不再算取消失败。
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

  // 确认在途时两个按钮都禁用，关闭只发生在没有在途确认时
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
