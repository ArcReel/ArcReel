import { useCallback, useEffect, useRef, useState } from "react";
import { API } from "@/api";
import { isDemoProject } from "@/onboarding/demo-project";
import type { VideoCapabilities } from "@/types";

interface UseVideoCapabilities {
  /** 解析成功的能力；未拉取完成、请求失败或演示项目时为 null。 */
  caps: VideoCapabilities | null;
  loading: boolean;
  /** 重新拉取一次（用户改过模型或能力覆盖后调用）。 */
  refresh: () => void;
}

/**
 * 当前项目生效的视频模型能力。
 *
 * `videoBackend` 参与请求 key 是为了让「换模型后门控随之更新」不依赖组件重挂载：
 * 项目级 backend 一变即重新解析。能力覆盖改的是供应商配置、不落项目字段，
 * 故额外暴露 `refresh` 供消费方在用户显式查看门控的时机（如展开设置面板）主动重取。
 *
 * 演示项目后端不存在，直接返回空能力而不发请求（对齐 StudioCanvasRouter 的时长解析）。
 *
 * 加载态由「已落地结果的 key 是否等于当前 key」派生，而非 effect 内同步 setState：
 * 后者会触发级联渲染（react-hooks/set-state-in-effect）。
 */
export function useVideoCapabilities(
  projectName: string | undefined | null,
  videoBackend?: string | null,
): UseVideoCapabilities {
  const [nonce, setNonce] = useState(0);
  const [result, setResult] = useState<{ key: string; caps: VideoCapabilities | null } | null>(
    null,
  );
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  const enabled = !!projectName && !isDemoProject(projectName);
  // key 只含「决定结果是否仍可用」的上下文：项目与后端。nonce 单独驱动重取，
  // 不进 key——同上下文的主动重取应保留旧值（否则每次展开面板都闪一次加载态、
  // 把控件短暂禁用），而换项目 / 换后端必须立刻丢弃旧能力，避免按过期值门控。
  const key = enabled ? `${projectName} ${videoBackend ?? ""}` : null;

  useEffect(() => {
    // 接管方轮换 controller：新一轮先作废前任，避免慢响应回写覆盖新值。
    abortRef.current?.abort();
    if (key === null || !projectName) {
      abortRef.current = null;
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    const { signal } = controller;
    API.getVideoCapabilities(projectName, { signal })
      .then((next) => {
        // 网络 await 之后的写 state 断点：abort 可能发生在响应已 resolve 之后。
        if (signal.aborted) return;
        setResult({ key, caps: next });
      })
      .catch(() => {
        if (signal.aborted) return;
        // 解析失败按「能力未知」处理：门控由消费方决定如何降级，不在此处编造能力值。
        setResult({ key, caps: null });
      });
    return () => {
      controller.abort();
    };
  }, [key, projectName, nonce]);

  const settled = key !== null && result?.key === key;
  return {
    caps: settled ? result.caps : null,
    loading: key !== null && !settled,
    refresh,
  };
}
