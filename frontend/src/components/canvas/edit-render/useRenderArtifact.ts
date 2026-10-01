import { useCallback, useEffect, useRef, useState } from "react";
import { API } from "@/api";
import { submitRender } from "@/actions/render";
import { errMsg } from "@/utils/async";
import { isTerminalStatus } from "@/types";
import type { FinalCutStatus, JianyingDraftStatus, RenderKind, TaskItem } from "@/types";

/** 出片任务的轮询间隔：对话框打开期间按 task_id 拉取进度，比任务列表的兜底轮询更及时。 */
export const RENDER_TASK_POLL_MS = 2000;

const RENDER_TASK_TYPE: Record<RenderKind, string> = {
  final_cut: "render_final_cut",
  jianying_draft: "render_jianying_draft",
};

export type RenderArtifactView = FinalCutStatus | JianyingDraftStatus;

export interface RenderArtifactState {
  /** 当前交付物的产物现状；首次加载完成前为 null。 */
  artifact: RenderArtifactView | null;
  loading: boolean;
  /** 读取现状失败的原因。 */
  loadError: string | null;
  /** 本次提交的任务；未提交时为 null，终态后保留以展示结果（失败原因在任务上）。 */
  task: TaskItem | null;
  /** 从提交到任务终态都为 true。 */
  submitting: boolean;
  /** 提交请求本身失败的原因（如入队前检查不通过）。 */
  submitError: string | null;
  /** 提交一次出片任务并开始轮询。 */
  submit: () => Promise<void>;
}

function fetchArtifact(
  projectName: string,
  timelineId: string,
  kind: RenderKind,
  signal: AbortSignal,
): Promise<RenderArtifactView> {
  return kind === "final_cut"
    ? API.getFinalCutStatus(projectName, timelineId, { signal })
    : API.getJianyingDraftStatus(projectName, timelineId, { signal });
}

/** 查找与产物同一剪辑时间线、同一交付物与版本的在途出片任务；查询失败时按没有处理。 */
async function findActiveRenderTask(
  projectName: string,
  kind: RenderKind,
  artifact: RenderArtifactView,
): Promise<TaskItem | null> {
  try {
    const pages = await Promise.all(
      (["running", "queued"] as const).map((status) =>
        API.listTasks({ projectName, taskType: RENDER_TASK_TYPE[kind], status }),
      ),
    );
    return (
      pages
        .flatMap((page) => page.items)
        .find(
          (task) =>
            task.payload.timeline_id === artifact.timeline_id &&
            task.payload.narration === artifact.narration &&
            (!("subtitles" in artifact) || task.payload.subtitles === artifact.subtitles),
        ) ?? null
    );
  } catch {
    return null;
  }
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(signal.reason instanceof Error ? signal.reason : new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

/**
 * 一个剪辑时间线某种交付物的出片状态：产物现状、提交与任务进度。
 *
 * 取消域是「项目 × 剪辑时间线 × 交付物」：任一变化或卸载时作废在途的读取与轮询。
 * 读到产物现状后，若同一产物已有在途任务（如对话框关闭前提交的，或由 Agent 提交的），接着跟踪它。
 * 任务成功后补拉一次产物现状，失败时展示任务的失败原因。
 */
export function useRenderArtifact(
  projectName: string,
  timelineId: string,
  kind: RenderKind,
): RenderArtifactState {
  const [artifact, setArtifact] = useState<RenderArtifactView | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [task, setTask] = useState<TaskItem | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const scopeRef = useRef<AbortController | null>(null);

  const load = useCallback(
    async (signal: AbortSignal): Promise<RenderArtifactView | null> => {
      setLoading(true);
      setLoadError(null);
      try {
        const next = await fetchArtifact(projectName, timelineId, kind, signal);
        if (signal.aborted) return null;
        setArtifact(next);
        return next;
      } catch (err) {
        if (signal.aborted) return null;
        setLoadError(errMsg(err));
        return null;
      } finally {
        if (!signal.aborted) setLoading(false);
      }
    },
    [projectName, timelineId, kind],
  );

  const poll = useCallback(
    async (taskId: string, signal: AbortSignal) => {
      for (;;) {
        const current = await API.getTask(taskId);
        if (signal.aborted) return;
        setTask(current);
        if (isTerminalStatus(current.status)) {
          if (current.status === "succeeded") await load(signal);
          return;
        }
        await sleep(RENDER_TASK_POLL_MS, signal);
      }
    },
    [load],
  );

  /** 从取得 task_id 到任务终态都处于 submitting；取 task_id 或轮询失败时记为提交失败。 */
  const follow = useCallback(
    async (start: () => Promise<string>, signal: AbortSignal) => {
      setSubmitting(true);
      setSubmitError(null);
      setTask(null);
      try {
        const taskId = await start();
        if (signal.aborted) return;
        await poll(taskId, signal);
      } catch (err) {
        if (signal.aborted) return;
        setSubmitError(errMsg(err));
      } finally {
        if (!signal.aborted) setSubmitting(false);
      }
    },
    [poll],
  );

  useEffect(() => {
    const controller = new AbortController();
    scopeRef.current = controller;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 切换交付物或剪辑时间线时清掉上一份的现状与任务，是有意的状态重置
    setArtifact(null);
    setTask(null);
    setSubmitError(null);
    setSubmitting(false);
    void (async () => {
      const loaded = await load(controller.signal);
      if (loaded === null || controller.signal.aborted) return;
      const active = await findActiveRenderTask(projectName, kind, loaded);
      if (active === null || controller.signal.aborted) return;
      await follow(() => Promise.resolve(active.task_id), controller.signal);
    })();
    return () => controller.abort();
  }, [load, follow, projectName, kind]);

  const submit = useCallback(async () => {
    const signal = scopeRef.current?.signal;
    if (!signal || signal.aborted) return;
    await follow(async () => (await submitRender(projectName, timelineId, kind)).task_id, signal);
  }, [projectName, timelineId, kind, follow]);

  return { artifact, loading, loadError, task, submitting, submitError, submit };
}
