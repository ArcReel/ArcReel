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
export function useTaskCancellation(
  scopeKey: string | null,
  onCancelled?: () => void | Promise<void>,
): TaskCancellation {
  // 确认态记在发起它的作用域之下：切换项目后旧项目的确认态（包括切换后才返回的旧预览）不会生效
  const [owned, setOwned] = useState<{ scope: string | null; request: CancelRequest } | null>(null);
  const request = owned?.scope === scopeKey ? owned.request : null;
  const [cancelling, setCancelling] = useState(false);
  const [failedState, setFailed] = useState(false);
  const [failureDetailState, setFailureDetail] = useState<string | null>(null);
  const failed = request !== null && failedState;
  const failureDetail = request !== null ? failureDetailState : null;

  // 切换作用域时清掉确认态：只靠上面的归属判断，切回原项目时旧确认会重新出现
  const [prevScopeKey, setPrevScopeKey] = useState(scopeKey);
  if (scopeKey !== prevScopeKey) {
    setPrevScopeKey(scopeKey);
    setOwned(null);
  }
  const [cancellingTaskIds, setCancellingTaskIds] = useState<ReadonlySet<string>>(new Set());
  const previewAbort = useRef<AbortController | null>(null);

  // 作废旧作用域在途的预览请求，它的结果不再写回
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
        setFailed(false);
        setFailureDetail(null);
        setOwned({ scope: scopeKey, request: { kind: "single", taskId, cascaded: preview.cascaded } });
      } catch {
        // 任务已不在可取消状态，或预览被更新的操作接管
      }
    },
    [beginPreview, scopeKey],
  );

  const requestAll = useCallback(
    async (projectName: string) => {
      const controller = beginPreview();
      try {
        const { queued_count } = await API.cancelAllPreview(projectName, {
          signal: controller.signal,
        });
        if (controller.signal.aborted || queued_count === 0) return;
        setFailed(false);
        setFailureDetail(null);
        setOwned({ scope: scopeKey, request: { kind: "all", projectName, queuedCount: queued_count } });
      } catch {
        // 没有排队中的任务，或预览被更新的操作接管
      }
    },
    [beginPreview, scopeKey],
  );

  const confirm = useCallback(async () => {
    if (!request) return;
    setCancelling(true);
    setFailed(false);
    setFailureDetail(null);
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
        setFailed(true);
        if (error instanceof ApiRequestError && error.status === 409) setFailureDetail(error.message);
        return;
      }
      // 取消已落地，确认态先收起；随后的重取失败是刷新问题，不再算取消失败。
      setOwned(null);
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
      setCancelling(false);
    }
  }, [request, onCancelled]);

  const dismiss = useCallback(() => {
    setOwned(null);
    setFailed(false);
    setFailureDetail(null);
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
