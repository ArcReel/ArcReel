import { useCallback, useEffect, useState } from "react";
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

/** 创建一次测试连接的载荷：内联定义或模型行引用二选一。 */
export interface TrialRunRequest {
  definition?: unknown;
  model_ref?: TrialRunModelRef;
  parameters: EndpointTestParameters;
  credentials?: EndpointTestCredentials;
}

export interface TrialRunHandle {
  run: TrialRunInfo | null;
  /** 终态：成功或失败。 */
  finished: boolean;
  starting: boolean;
  /** 创建、回读或取产物时的出错说明。 */
  error: string | null;
  /** 上一次 run 是被用户放弃的。 */
  cancelled: boolean;
  /** 回读连续失败到上限，已停止轮询；run 仍占着服务端名额，取消入口照旧可用。 */
  pollStopped: boolean;
  /** 产物的本地对象 URL；没有产物或还没取回为 null。 */
  artifactUrl: string | null;
  start: (request: TrialRunRequest, assets?: EndpointTestAssets) => Promise<void>;
  cancel: () => Promise<void>;
}

/**
 * 一次测试连接的生命周期：创建 → 轮询回读 → 取回产物 → 取消。
 *
 * 两种 kind 的测试连接卡各有自己的参数、素材与展示，但这条生命周期完全一致——轮询的放弃计数、
 * 404 的就地清空、取消后的名额释放都是踩过的坑，抄第二份等于把坑也抄一遍。
 */
export function useTrialRun(): TrialRunHandle {
  const [starting, setStarting] = useState(false);
  const [run, setRun] = useState<TrialRunInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [artifact, setArtifact] = useState<{ runId: string; url: string } | null>(null);
  const [cancelled, setCancelled] = useState(false);
  // 轮询放弃态：连续失败达上限后只停读回，不动 run——服务端名额仍被占用，
  // runId 与取消入口必须保留，否则重新创建会撞 trial_run_already_running。
  const [pollStopped, setPollStopped] = useState(false);

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

  // 试跑是进程内异步 run：创建后轮询读回，终态即停。递归 setTimeout 保证上一次
  // 读回落地后才排下一次，响应慢于间隔时不会堆积并发请求。
  useEffect(() => {
    if (!runId || finished || pollStopped) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    // 连续失败达到上限即停：run 被删或凭证过期时 401/404 不会自愈，无限重试只会刷请求。
    // 单次失败不弃轮询，瞬时网络抖动在下一轮成功后计数归零。
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
          // 明确的 404 表示 run 已不在服务端（TTL 过期或重启丢失），名额已释放，
          // 就地清空本地状态；只有瞬时网络/服务错误才走重试与放弃计数。
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

  // 取消会让服务端连同结果一起丢弃这次 run，回读只会拿到 404；就地清空本地状态，
  // 让「开始测试」重新可用。远端任务不受影响，已经发生的费用照算。
  const cancel = useCallback(async () => {
    if (!runId) return;
    try {
      await API.cancelTrialRun(runId);
    } catch (e) {
      // 404 即 run 已不在服务端，无可取消——照常清理本地状态解除锁定。
      if (!(e instanceof ApiRequestError && e.status === 404)) {
        setError(errMsg(e));
        return;
      }
    }
    setRun(null);
    setError(null);
    setPollStopped(false);
    setCancelled(true);
  }, [runId]);

  return { run, finished, starting, error, cancelled, pollStopped, artifactUrl, start, cancel };
}
