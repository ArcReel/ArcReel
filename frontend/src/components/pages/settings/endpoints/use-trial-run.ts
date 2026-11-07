import { useCallback, useEffect, useRef, useState } from "react";
import { API, ApiRequestError } from "@/api";
import { errMsg } from "@/utils/async";
import type {
  EndpointTestAssets,
  EndpointTestCredentials,
  EndpointTestParameters,
  TrialRunInfo,
  TrialRunModelRef,
} from "@/types";

const TRIAL_POLL_INTERVAL_MS = 2000;
const TRIAL_POLL_MAX_CONSECUTIVE_FAILURES = 5;

/** Connection-test creation payloads: inline definitions or model-row references, mutually exclusive. */
export interface TrialRunRequest {
  definition?: unknown;
  model_ref?: TrialRunModelRef;
  parameters: EndpointTestParameters;
  credentials?: EndpointTestCredentials;
}

export interface TrialRunHandle {
  run: TrialRunInfo | null;
  /** Terminal success/failure states. */
  finished: boolean;
  starting: boolean;
  /** Creation/readback/output-retrieval errors. */
  error: string | null;
  /** Previous runs abandoned by users. */
  cancelled: boolean;
  /** Consecutive read failures reached limits, stopping polling; runs still occupy server slots, retaining cancellation. */
  pollStopped: boolean;
  /** Local output object URLs; null without outputs or before retrieval. */
  artifactUrl: string | null;
  start: (request: TrialRunRequest, assets?: EndpointTestAssets) => Promise<void>;
  cancel: () => Promise<void>;
}

/**
 * Connection-test lifecycle: create → poll readback → retrieve outputs → cancel.
 *
 * Both kinds' test cards own parameters/materials/presentation, but lifecycles are identical. Poll-abandonment counts,
 * in-place 404 cleanup and postcancel slot release already exposed bugs; duplicating lifecycle duplicates pitfalls.
 */
export function useTrialRun(): TrialRunHandle {
  const [starting, setStarting] = useState(false);
  const [run, setRun] = useState<TrialRunInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [artifact, setArtifact] = useState<{ runId: string; url: string } | null>(null);
  const [cancelled, setCancelled] = useState(false);
  // Poll abandonment stops reads after failure limits without touching runs, whose server slots remain occupied;
  // retain runId/cancellation or new creation hits trial_run_already_running.
  const [pollStopped, setPollStopped] = useState(false);
  // Increment per start: old runs may finish during cancellation/readback and users start new ones; late results cannot overwrite new runs.
  const generation = useRef(0);

  const runId = run?.id ?? null;
  const finished = run !== null && (run.status === "succeeded" || run.status === "failed");
  const artifactRunId = run?.has_artifact ? run.id : null;
  const artifactUrl = artifact?.runId === artifactRunId ? artifact.url : null;

  useEffect(() => {
    if (!artifactRunId) return;
    const controller = new AbortController();
    let objectUrl: string | null = null;
    void API.getTrialRunArtifact(artifactRunId, { signal: controller.signal })
      .then((blob) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setArtifact({ runId: artifactRunId, url: objectUrl });
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(errMsg(e));
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [artifactRunId]);

  // Trials are process-local async runs: create, poll until terminal, then stop. Recursive setTimeout schedules only after
  // prior read settlement, preventing concurrent accumulation when responses exceed intervals.
  // react-doctor-disable-next-line react-doctor/effect-needs-cleanup -- every timer uses the same timer variable; cleanup clearTimeout/abort, with aborted callbacks scheduling nothing
  useEffect(() => {
    if (!runId || finished || pollStopped) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    // Stop at consecutive failure limits: deleted runs/expired credentials produce nonhealing 401/404; infinite retries only flood requests.
    // Single failures retain polling; next success clears transient-network counters.
    let consecutiveFailures = 0;
    const poll = () => {
      void API.getTrialRun(runId, { signal: controller.signal })
        .then((next) => {
          if (controller.signal.aborted) return;
          consecutiveFailures = 0;
          setError(null);
          setRun(next);
          timer = setTimeout(poll, TRIAL_POLL_INTERVAL_MS);
        })
        .catch((e) => {
          if (controller.signal.aborted) return;
          setError(errMsg(e));
          // Explicit 404 means absent server runs (TTL/restarts), releasing slots;
          // clear local state immediately. Only transient network/server failures use retry/abandonment counts.
          if (e instanceof ApiRequestError && e.status === 404) {
            setRun(null);
            return;
          }
          consecutiveFailures += 1;
          if (consecutiveFailures < TRIAL_POLL_MAX_CONSECUTIVE_FAILURES) {
            timer = setTimeout(poll, TRIAL_POLL_INTERVAL_MS);
          } else {
            setPollStopped(true);
          }
        });
    };
    timer = setTimeout(poll, TRIAL_POLL_INTERVAL_MS);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [runId, finished, pollStopped]);

  const start = useCallback(async (request: TrialRunRequest, assets: EndpointTestAssets = {}) => {
    generation.current += 1;
    setError(null);
    setCancelled(false);
    setPollStopped(false);
    setStarting(true);
    try {
      setRun(await API.createTrialRun(request, assets));
    } catch (e) {
      setRun(null);
      setError(errMsg(e));
    } finally {
      setStarting(false);
    }
  }, []);

  // Cancellation discards server runs/results, leaving readback 404. Clear local state
  // to enable Start test. Remote jobs remain unaffected; incurred costs still apply.
  const cancel = useCallback(async () => {
    if (!runId) return;
    const started = generation.current;
    const superseded = () => generation.current !== started;
    try {
      await API.cancelTrialRun(runId);
    } catch (e) {
      if (superseded()) return;
      if (!(e instanceof ApiRequestError && e.status === 404)) {
        setError(errMsg(e));
        return;
      }
      // Two 404 meanings: provider returned before late cancellation, retaining server results; or expired absent runs.
      // Read once first, showing retained results and polling nonterminal runs; only readback 404 clears as cancelled.
      try {
        const kept = await API.getTrialRun(runId);
        if (superseded()) return;
        setRun(kept);
        setPollStopped(false);
        setError(null);
        return;
      } catch (readError) {
        if (superseded()) return;
        if (!(readError instanceof ApiRequestError && readError.status === 404)) {
          setError(errMsg(readError));
          return;
        }
      }
    }
    if (superseded()) return;
    setRun(null);
    setError(null);
    setPollStopped(false);
    setCancelled(true);
  }, [runId]);

  return { run, finished, starting, error, cancelled, pollStopped, artifactUrl, start, cancel };
}
