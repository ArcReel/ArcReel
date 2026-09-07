import { useCallback, useEffect, useRef, useState } from "react";

import { API } from "@/api";

interface CascadeTask {
  task_id: string;
  task_type: string;
  resource_id: string;
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
  /** 取消中的任务 id；行内把 × 换成 spinner 用它判定。 */
  cancellingTaskIds: ReadonlySet<string>;
  requestSingle: (taskId: string) => Promise<void>;
  requestAll: (projectName: string) => Promise<void>;
  confirm: () => Promise<void>;
  dismiss: () => void;
}

/**
 * 取消任务的两步交互：先取预览、再由 `alertdialog` 二次确认。语义同 ADR 0006——
 * 单个取消预览会被级联取消的下游任务，全部取消只清排队中、不动运行中。
 *
 * 预览接口在任务已离开可取消状态时报错，此时静默收场：这一行马上会被下一轮任务刷新
 * 改写，弹一个「取消失败」反而让用户以为自己漏点了。
 */
export function useTaskCancellation(
  scopeKey: string | null,
  onCancelled?: () => void | Promise<void>,
): TaskCancellation {
  const [request, setRequest] = useState<CancelRequest | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [cancellingTaskIds, setCancellingTaskIds] = useState<ReadonlySet<string>>(new Set());
  const previewAbort = useRef<AbortController | null>(null);

  useEffect(() => {
    previewAbort.current?.abort();
    previewAbort.current = null;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 切换项目后旧项目的确认请求必须同步消失
    setRequest(null);
  }, [scopeKey]);

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
        setRequest({ kind: "single", taskId, cascaded: preview.cascaded });
      } catch {
        // 任务已不在可取消状态，或预览被更新的操作接管
      }
    },
    [beginPreview],
  );

  const requestAll = useCallback(
    async (projectName: string) => {
      const controller = beginPreview();
      try {
        const { queued_count } = await API.cancelAllPreview(projectName, {
          signal: controller.signal,
        });
        if (controller.signal.aborted || queued_count === 0) return;
        setRequest({ kind: "all", projectName, queuedCount: queued_count });
      } catch {
        // 没有排队中的任务，或预览被更新的操作接管
      }
    },
    [beginPreview],
  );

  const confirm = useCallback(async () => {
    if (!request) return;
    setCancelling(true);
    if (request.kind === "single") {
      const taskId = request.taskId;
      setCancellingTaskIds((prev) => new Set(prev).add(taskId));
    }
    try {
      if (request.kind === "single") await API.cancelTask(request.taskId);
      else await API.cancelAllQueued(request.projectName);
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
      setRequest(null);
    }
  }, [request, onCancelled]);

  const dismiss = useCallback(() => setRequest(null), []);

  return { request, cancelling, cancellingTaskIds, requestSingle, requestAll, confirm, dismiss };
}
