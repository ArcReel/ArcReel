import { useEffect, useMemo, useRef, useState } from "react";
import { API } from "@/api";
import { isDemoProject } from "@/onboarding/demo-project";
import { useCapabilitiesStore } from "@/stores/capabilities-store";
import {
  constrainDurations,
  lookupDurationConstraints,
  lookupSupportedDurations,
  type DurationConstraints,
} from "@/utils/provider-models";
import type { CustomProviderInfo, ProviderInfo, VideoCapabilities } from "@/types";

// ---------------------------------------------------------------------------
// 视频模型能力：前端唯一的能力消费入口。
//
// 每个能力维度只有一个真相源，规则写在这里而非各调用点——否则每新增一维都要在
// 「静态目录」与「/video-capabilities」之间重新选边，且目录一侧永远感知不到能力覆盖：
//
//   firstFrame / lastFrame  → 服务端生效值。这两维带用户覆盖语义（覆盖存在供应商配置上、
//                             不落项目字段），只有服务端能给出「系统判定 ⊕ 用户覆盖」的结果。
//   durations               → 静态目录的 supported_durations，再经分辨率 / 参考图两条联动
//                             约束收窄。约束只声明在目录里、服务端不返回，故以目录为准；
//                             目录解析不出候选模型时（后端留空 → 服务端 auto 解析，或存值
//                             已不在目录中）退回服务端解析出的 supported_durations。
//   durationConstraints     → 目录。纯联动声明，无覆盖语义。
//
// 目录侧同步可得，故时长在首帧即有值、不闪加载态；服务端只在目录缺项时才影响结果。
// ---------------------------------------------------------------------------

const EMPTY_PROVIDERS: ProviderInfo[] = [];
const EMPTY_CUSTOM_PROVIDERS: CustomProviderInfo[] = [];
const EMPTY_CONSTRAINTS: DurationConstraints = { byResolution: {}, withReferenceImages: [] };

/** 时长联动约束的上下文：两条约束各自独立触发、可同时生效。 */
export interface DurationContext {
  videoResolution?: string | null;
  usesReferenceImages?: boolean;
}

export interface ModelCapabilitiesInput extends DurationContext {
  /** 项目名；缺省（如创建向导，项目尚不存在）时不查服务端，仅走目录。 */
  projectName?: string | null;
  /**
   * 视频后端 "provider/model"；空表示跟随全局默认，由服务端解析。
   *
   * 目录侧按它查表，故任意候选模型都问得动。服务端侧只拿它当缓存 key：端点不收该参数、
   * 一律按已落盘的 project.json 解析，因此 `firstFrame` / `lastFrame` 描述的是**项目当前
   * 保存的**后端。传入编辑中的未保存值（设置页）时这两维不作数，调用方不得消费。
   */
  videoBackend?: string | null;
  providers?: ProviderInfo[];
  customProviders?: CustomProviderInfo[];
  /** 置 false 时既不查服务端也不查目录（演示态等）。 */
  enabled?: boolean;
}

export interface ModelCapabilities {
  /** 收窄前的时长全集；未知为 null。判定越界成因时用它区分「模型不支持」与「被约束收窄」。 */
  rawDurations: number[] | null;
  /** 经联动约束收窄并升序排列的时长候选；未知为 null。 */
  supportedDurations: number[] | null;
  durationConstraints: DurationConstraints;
  /** 首帧 / 尾帧生效值（含用户覆盖）；尚未查到或查询失败时为 null（未知），不谎报不支持。 */
  firstFrame: boolean | null;
  lastFrame: boolean | null;
  /** 服务端能力查询在途。目录侧不受影响，时长仍即时可用。 */
  loading: boolean;
}

/**
 * 目录侧的时长候选，收窄后升序返回；目录查不到该模型为 null。
 *
 * 与 hook 同源同规则的纯函数形态，供「对着另一个候选模型算一次」的场景使用——例如切换模型
 * 时判断已选时长在新模型下是否仍成立，这发生在事件处理器里，取不到 hook 的返回值。
 */
export function catalogDurations(
  providers: ProviderInfo[],
  customProviders: CustomProviderInfo[],
  videoBackend: string,
  ctx: DurationContext = {},
): number[] | null {
  if (!videoBackend) return null;
  const raw = lookupSupportedDurations(providers, videoBackend, customProviders);
  if (!raw?.length) return null;
  return narrow(raw, lookupDurationConstraints(providers, videoBackend), ctx);
}

function narrow(raw: readonly number[], constraints: DurationConstraints, ctx: DurationContext) {
  return constrainDurations(raw, constraints, {
    resolution: ctx.videoResolution,
    usesReferenceImages: ctx.usesReferenceImages,
  }).sort((a, b) => a - b);
}

/**
 * 服务端能力查询。
 *
 * 请求 key 只含「决定结果是否仍可用」的上下文：项目与后端。项目 / 后端一变必须立刻丢弃旧
 * 能力，避免按过期值门控；revision 则单独驱动重取而不进 key——能力覆盖改的是供应商配置，
 * 旧值在新值到达前仍是当前最优估计，进 key 会让每次覆盖变更都闪一次加载态、短暂灰掉控件。
 *
 * 加载态由「已落地结果的 key 是否等于当前 key」派生，而非 effect 内同步 setState：
 * 后者会触发级联渲染（react-hooks/set-state-in-effect）。
 */
function useResolvedCapabilities(
  projectName: string | undefined | null,
  videoBackend: string | undefined | null,
  enabled: boolean,
): { caps: VideoCapabilities | null; loading: boolean } {
  const revision = useCapabilitiesStore((s) => s.revision);
  const [result, setResult] = useState<{ key: string; caps: VideoCapabilities | null } | null>(
    null,
  );
  const abortRef = useRef<AbortController | null>(null);

  // 演示项目后端不存在，直接返回空能力而不发请求。
  const active = enabled && !!projectName && !isDemoProject(projectName);
  const key = active ? `${projectName} ${videoBackend ?? ""}` : null;

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
  }, [key, projectName, revision]);

  const settled = key !== null && result?.key === key;
  return { caps: settled ? result.caps : null, loading: key !== null && !settled };
}

export function useModelCapabilities({
  projectName,
  videoBackend,
  providers = EMPTY_PROVIDERS,
  customProviders = EMPTY_CUSTOM_PROVIDERS,
  videoResolution,
  usesReferenceImages,
  enabled = true,
}: ModelCapabilitiesInput): ModelCapabilities {
  const { caps, loading } = useResolvedCapabilities(projectName, videoBackend, enabled);

  const durationConstraints = useMemo(
    () =>
      enabled && videoBackend
        ? lookupDurationConstraints(providers, videoBackend)
        : EMPTY_CONSTRAINTS,
    [enabled, providers, videoBackend],
  );

  const rawDurations = useMemo<number[] | null>(() => {
    if (!enabled) return null;
    const fromCatalog = videoBackend
      ? lookupSupportedDurations(providers, videoBackend, customProviders)
      : undefined;
    if (fromCatalog?.length) return fromCatalog;
    return caps?.supported_durations?.length ? caps.supported_durations : null;
  }, [enabled, providers, customProviders, videoBackend, caps]);

  const supportedDurations = useMemo<number[] | null>(
    () =>
      rawDurations
        ? narrow(rawDurations, durationConstraints, { videoResolution, usesReferenceImages })
        : null,
    [rawDurations, durationConstraints, videoResolution, usesReferenceImages],
  );

  return {
    rawDurations,
    supportedDurations,
    durationConstraints,
    firstFrame: caps ? caps.first_frame : null,
    lastFrame: caps ? caps.last_frame : null,
    loading,
  };
}
