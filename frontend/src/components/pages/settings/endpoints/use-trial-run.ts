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

export interface TrialRunRequest {
  definition?: unknown;
  model_ref?: TrialRunModelRef;
  parameters: EndpointTestParameters;
  credentials?: EndpointTestCredentials;
}

export interface TrialRunHandle {
  run: TrialRunInfo | null;
  finished: boolean;
  starting: boolean;
  error: string | null;
  cancelled: boolean;
  pollStopped: boolean;
  artifactUrl: string | null;
  start: (request: TrialRunRequest, assets?: EndpointTestAssets) => Promise<void>;
  cancel: () => Promise<void>;
}

export function useTrialRun(): TrialRunHandle {
  const [starting, setStarting] = useState(false);
  const [run, setRun] = useState<TrialRunInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [artifact, setArtifact] = useState<{ runId: string; url: string } | null>(null);
  const [cancelled, setCancelled] = useState(false);
  const [pollStopped, setPollStopped] = useState(false);
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

  // react-doctor-disable-next-line react-doctor/effect-needs-cleanup -- 每轮的定时器号写进同一个 timer，cleanup 统一 clearTimeout 并 abort；abort 后的回调不再排下一轮
  useEffect(() => {
    if (!runId || finished || pollStopped) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
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
