import { useCallback, useEffect, useRef, useState } from "react";
import { API } from "@/api";
import { submitRender } from "@/actions/render";
import { errMsg } from "@/utils/async";
import { isTerminalStatus } from "@/types";
import type { FinalCutStatus, JianyingDraftStatus, RenderKind, RenderOptions, SubtitleMode, TaskItem } from "@/types";
import type { TimelineNarration } from "@/types/edit-timeline";

/** Render-task polling interval: query task_id while dialogs open, timelier than fallback task-list polling. */
export const RENDER_TASK_POLL_MS = 2000;

const RENDER_TASK_TYPE: Record<RenderKind, string> = {
  final_cut: "render_final_cut",
  jianying_draft: "render_jianying_draft",
};

export type RenderArtifactView = FinalCutStatus | JianyingDraftStatus;

export interface RenderArtifactState {
  /** Current deliverable artifact state, null until first load completes. */
  artifact: RenderArtifactView | null;
  loading: boolean;
  /** State-read failure reason. */
  loadError: string | null;
  /** Submitted task, null before submission; retain after terminal states to show results/failure reasons on task. */
  task: TaskItem | null;
  /** True from submission through task terminal state. */
  submitting: boolean;
  /** True during submission before receiving task_id; reconnecting existing tasks skips this phase. */
  requesting: boolean;
  /** Submission-request failure reason, e.g. preenqueue checks. */
  submitError: string | null;
  /** Submit a render task and begin polling. */
  submit: () => Promise<void>;
}

function fetchArtifact(
  projectName: string,
  timelineId: string,
  kind: RenderKind,
  options: RenderOptions,
  signal: AbortSignal,
): Promise<RenderArtifactView> {
  return kind === "final_cut"
    ? API.getFinalCutStatus(projectName, timelineId, { ...options, signal })
    : API.getJianyingDraftStatus(projectName, timelineId, { narration: options.narration, signal });
}

/** Find active render tasks matching timeline/deliverable/variant; failed queries mean none. */
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
 * Rendering state for one timeline/deliverable/variant: artifact state, submission, and task progress.
 * Variant combines narration and burned subtitles; Jianying drafts ignore ``subtitles``.
 *
 * Cancellation scope is project × timeline × deliverable × variant: changes/unmount invalidate active reads/polls.
 * Callers key consumers with these values so switches fully remount, preventing local-state leaks across variants.
 * After reading artifact state, reconnect to active same-artifact tasks, e.g. submitted before dialog closing or by Agents.
 * Successful tasks refetch artifact state; failures display task failure reasons.
 */
export function useRenderArtifact(
  projectName: string,
  timelineId: string,
  kind: RenderKind,
  narration: TimelineNarration,
  subtitles: SubtitleMode,
): RenderArtifactState {
  const [artifact, setArtifact] = useState<RenderArtifactView | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [task, setTask] = useState<TaskItem | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const scopeRef = useRef<AbortController | null>(null);

  const load = useCallback(
    async (signal: AbortSignal): Promise<RenderArtifactView | null> => {
      setLoading(true);
      setLoadError(null);
      try {
        const next = await fetchArtifact(projectName, timelineId, kind, { narration, subtitles }, signal);
        if (signal.aborted) return null;
        setArtifact(next);
        return next;
      } catch (err) {
        if (signal.aborted) return null;
        setLoadError(errMsg(err));
        return null;
      } finally {
        // react-doctor-disable-next-line react-doctor/no-loading-flag-reset-outside-finally -- Already reset in finally; abort hands reset responsibility to the replacing load
        if (!signal.aborted) setLoading(false);
      }
    },
    [projectName, timelineId, kind, narration, subtitles],
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

  /** Remain submitting from task_id receipt to terminal state; task_id/poll failures count as submission failures. */
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
        // react-doctor-disable-next-line react-doctor/no-loading-flag-reset-outside-finally -- Already reset in finally; abort invalidates cancellation scope, leaving reset to the new owner
        if (!signal.aborted) setSubmitting(false);
      }
    },
    [poll],
  );

  useEffect(() => {
    const controller = new AbortController();
    scopeRef.current = controller;
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
    await follow(async () => {
      setRequesting(true);
      try {
        return (await submitRender(projectName, timelineId, kind, { narration, subtitles })).task_id;
      } finally {
        if (!signal.aborted) setRequesting(false);
      }
    }, signal);
  }, [projectName, timelineId, kind, narration, subtitles, follow]);

  return { artifact, loading, loadError, task, submitting, requesting, submitError, submit };
}
