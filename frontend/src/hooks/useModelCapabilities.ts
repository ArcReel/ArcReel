import { useEffect, useRef, useState } from "react";
import { API } from "@/api";
import { isDemoProject } from "@/onboarding/demo-project";
import { useCapabilitiesStore } from "@/stores/capabilities-store";
import type { DurationExclusionReason, VideoCapabilities, VoiceConsistencyTier } from "@/types";

// ---------------------------------------------------------------------------
// 视频模型能力：前端唯一的能力消费入口，单通路读服务端 video-capabilities 端点。
//
// 每个维度都取服务端值，前端不持有任何规则副本：
//
//   firstFrame / lastFrame  → 生效值（系统判定 ⊕ 用户覆盖），只有服务端能给出。
//   voiceConsistency        → 服务端二维派生（模型能力 × 项目生成模式）。
//   durations               → 型号声明全集 + 按上下文收窄后的候选与剔除成因，均由服务端
//                             `duration_constraints_report` 算好；分辨率↔时长、参考图↔时长的
//                             收窄规则只在 lib/config/resolver.py 一处。
//
// 有项目时走 /projects/{name}/video-capabilities（可带表单里未保存的候选模型与约束上下文），
// 无项目（创建向导）走 /providers/video-capabilities 按候选模型解析。
// ---------------------------------------------------------------------------

export interface ModelCapabilitiesInput {
  /** 项目名；缺省（如创建向导，项目尚不存在）时按 `videoBackend` 走无项目端点。 */
  projectName?: string | null;
  /**
   * 视频后端 "provider/model"；有项目时空表示跟随已落盘配置由服务端解析，无项目时空则不查。
   * 表单里编辑中的未保存候选同样传这里，服务端按候选模型 × 项目生成模式解析。
   */
  videoBackend?: string | null;
  /**
   * 时长收窄用的分辨率：undefined = 服务端按项目已保存档位（工作台）；null = 表单里显式选了
   * 「自动」，不回退到已保存档位；字符串 = 按该档位（表单里未保存的选择）。
   */
  videoResolution?: string | null;
  /** 是否按参考图路径收窄；undefined = 服务端按项目生成模式判定。 */
  usesReferenceImages?: boolean;
  /** 置 false 时不查（演示态等）。 */
  enabled?: boolean;
}

export interface ModelCapabilities {
  /** 型号声明的时长全集（升序）；未知为 null。判定越界成因时用它区分「模型不支持」与「被约束收窄」。 */
  rawDurations: number[] | null;
  /** 按当前上下文收窄后的时长候选（升序）；未知为 null。 */
  supportedDurations: number[] | null;
  /**
   * 同分辨率下不走参考图路径的收窄结果；未知为 null。参考生视频的参考图约束按视频单元是否
   * 真的携带参考图生效，画布为无参考图的单元换用它。
   */
  supportedDurationsWithoutReference: number[] | null;
  /** 全集中被联动约束剔除的时长（键为秒数字符串）→ 成因；未知为空表。 */
  excludedDurations: Record<string, DurationExclusionReason>;
  /**
   * 能力实际查自哪个 `provider/model`；未知为 null。
   *
   * 传入的后端可能是裸 provider（服务端补全默认视频模型）或留空跟随全局默认，此时该值取服务端
   * 解析结果。凡按「项目为该后端保存了什么」查项目配置的调用点都须用它。
   */
  resolvedVideoBackend: string | null;
  /** 首帧 / 尾帧生效值（含用户覆盖）；尚未查到或查询失败时为 null（未知），不谎报不支持。 */
  firstFrame: boolean | null;
  lastFrame: boolean | null;
  /** 声音一致性三级标识；尚未查到或查询失败时为 null（未知）。 */
  voiceConsistency: VoiceConsistencyTier | null;
  /** 当前上下文的查询在途（含约束上下文变化后的重取）。 */
  loading: boolean;
}

/**
 * 已保存时长越界的成因；不越界为 null。
 *
 * 成因决定提示该把用户引向哪里：`model` 只能换时长或换模型，`resolution` / `reference` 则是
 * 改对应设置也能解决。全集就不含该值时报 `model`；否则查服务端给出的剔除表。
 */
export type DurationOutOfRangeReason = "model" | DurationExclusionReason;

export function durationOutOfRangeReason(
  saved: number | null | undefined,
  caps: Pick<ModelCapabilities, "rawDurations" | "supportedDurations" | "excludedDurations">,
): DurationOutOfRangeReason | null {
  const { rawDurations, supportedDurations, excludedDurations } = caps;
  if (saved == null || !supportedDurations || supportedDurations.includes(saved)) return null;
  if (!rawDurations?.includes(saved)) return "model";
  return excludedDurations[String(saved)] ?? "model";
}

const EMPTY_EXCLUSIONS: Record<string, DurationExclusionReason> = {};

function ascending(values: readonly number[]): number[] {
  return [...values].sort((a, b) => a - b);
}

/**
 * 请求身份 key 只含「决定结果是否仍可用」的上下文：项目与后端。这两项一变必须立刻丢弃旧
 * 能力，避免按过期值门控。约束上下文（分辨率 / 参考图路径）与 revision 则只驱动重取而不进
 * 身份 key——同一模型下旧的收窄结果仍是当前最优估计，进 key 会让表单每切一次分辨率就闪一次
 * 加载态、时长选择器整块消失再出现。
 *
 * 加载态由「已落地结果的 key 是否等于当前 key」派生，而非 effect 内同步 setState：
 * 后者会触发级联渲染（react-hooks/set-state-in-effect）。
 */
export function useModelCapabilities({
  projectName,
  videoBackend,
  videoResolution,
  usesReferenceImages,
  enabled = true,
}: ModelCapabilitiesInput): ModelCapabilities {
  const revision = useCapabilitiesStore((s) => s.revision);
  const [result, setResult] = useState<{
    key: string;
    contextKey: string;
    caps: VideoCapabilities | null;
  } | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // 演示项目后端不存在，直接返回空能力而不发请求；无项目时没有候选模型也无从查起。
  const active = enabled && (projectName ? !isDemoProject(projectName) : !!videoBackend);
  // 元组编码而非拼接：拼接的分隔符可能出现在字段内，("a b", "c") 与 ("a", "b c") 会撞成同一 key，
  // 切换时把前一组的结果当作本组已落地。
  const key = active ? JSON.stringify([projectName ?? null, videoBackend ?? ""]) : null;
  // undefined（跟随项目）与 null（显式自动）是两种不同请求，JSON 会把数组里的 undefined 写成 null，
  // 故单独用一位标记区分。
  const contextKey = JSON.stringify([
    videoResolution === undefined,
    videoResolution ?? null,
    usesReferenceImages ?? null,
  ]);

  useEffect(() => {
    // 接管方轮换 controller：新一轮先作废前任，避免慢响应回写覆盖新值。
    abortRef.current?.abort();
    if (key === null) {
      abortRef.current = null;
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    const { signal } = controller;
    const query = {
      signal,
      resolution: videoResolution,
      usesReferenceImages,
    };
    // 带上 videoBackend：表单里编辑中的候选模型也要拿到它自己的能力，否则服务端按已落盘
    // 配置解析，档位等二维派生值会停留在上一次保存的模型上。
    const request = projectName
      ? API.getVideoCapabilities(projectName, { ...query, videoBackend: videoBackend || undefined })
      : API.getModelVideoCapabilities(videoBackend ?? "", query);
    request
      .then((next) => {
        // 网络 await 之后的写 state 断点：abort 可能发生在响应已 resolve 之后。
        if (signal.aborted) return;
        setResult({ key, contextKey, caps: next });
      })
      .catch(() => {
        if (signal.aborted) return;
        // 解析失败按「能力未知」处理：门控由消费方决定如何降级，不在此处编造能力值。
        setResult({ key, contextKey, caps: null });
      });
    return () => {
      controller.abort();
    };
    // projectName / videoBackend 已编码进 key，videoResolution / usesReferenceImages 已编码进
    // contextKey，列出只为满足 exhaustive-deps，不引入额外请求。
  }, [key, contextKey, projectName, videoBackend, videoResolution, usesReferenceImages, revision]);

  const settled = key !== null && result?.key === key;
  const caps = settled ? result.caps : null;
  const fresh = settled && result.contextKey === contextKey;
  const constraints = caps?.duration_constraints ?? null;

  return {
    rawDurations: caps?.supported_durations?.length ? ascending(caps.supported_durations) : null,
    supportedDurations: constraints ? constraints.allowed : null,
    supportedDurationsWithoutReference: constraints ? constraints.allowed_without_reference_images : null,
    excludedDurations: constraints?.excluded ?? EMPTY_EXCLUSIONS,
    resolvedVideoBackend: caps ? `${caps.provider_id}/${caps.model}` : null,
    firstFrame: caps ? caps.first_frame : null,
    lastFrame: caps ? caps.last_frame : null,
    voiceConsistency: caps ? caps.voice_consistency : null,
    loading: key !== null && !fresh,
  };
}
