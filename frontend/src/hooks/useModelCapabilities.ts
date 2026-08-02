import { useEffect, useMemo, useRef, useState } from "react";
import { API } from "@/api";
import { isDemoProject } from "@/onboarding/demo-project";
import { useCapabilitiesStore } from "@/stores/capabilities-store";
import { useProjectsStore } from "@/stores/projects-store";
import { effectiveMode, type GenerationMode } from "@/utils/generation-mode";
import {
  constrainDurations,
  lookupDurationConstraints,
  lookupSupportedDurations,
  type DurationConstraints,
} from "@/utils/provider-models";
import type { CustomProviderInfo, ProviderInfo, VideoCapabilities, VoiceConsistencyTier } from "@/types";

// ---------------------------------------------------------------------------
// 视频模型能力：前端唯一的能力消费入口。
//
// 每个能力维度只有一个真相源，规则写在这里而非各调用点——否则每新增一维都要在
// 「静态目录」与「/video-capabilities」之间重新选边，且目录一侧永远感知不到能力覆盖：
//
//   firstFrame / lastFrame  → 服务端生效值。这两维带用户覆盖语义（覆盖存在供应商配置上、
//                             不落项目字段），只有服务端能给出「系统判定 ⊕ 用户覆盖」的结果。
//   voiceConsistency        → 服务端派生值。二维派生（模型能力 × 项目 generation_mode）只在
//                             服务端一处，前端不复制公式；无项目上下文时读目录端点的同名字段。
//   durations               → 静态目录的 supported_durations，再经分辨率 / 参考图两条联动
//                             约束收窄。约束只声明在目录里、服务端不返回，故以目录为准；
//                             目录解析不出候选模型时（后端留空 → 服务端 auto 解析，或存值已不
//                             在目录中 → resolver 按执行层规则回退到默认模型）退回服务端解析
//                             出的 supported_durations，`unsavedBackend` 为例外（见该字段）。
//   durationConstraints     → 目录。纯联动声明，无覆盖语义。按时长的实际来源模型查——走服务端
//                             回退时模型身份以服务端返回的为准，否则约束与时长会各描述一个模型。
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
   * 目录侧按它查表，服务端侧也会收到它（作为 `video_backend` 查询参数），故 `firstFrame` /
   * `lastFrame` / `voiceConsistency` 描述的都是这个候选模型，编辑中的未保存值同样对得上。
   */
  videoBackend?: string | null;
  /**
   * `videoBackend` 是表单里编辑中的未保存候选值时置 true。
   *
   * 只影响时长：目录查不到该候选模型时不回退到服务端解析结果，否则会把服务端解析出的另一个
   * 模型的时长摆成本候选的选项，用户能存下新模型不支持的值。
   */
  unsavedBackend?: boolean;
  /**
   * 当前查看的集号；生成模式可被单集覆盖，传了服务端才按该集生效模式解析
   * `voiceConsistency` / `firstFrame` 等二维派生值。设置页等无集号上下文的调用不传。
   */
  episode?: number | null;
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
  /**
   * 时长与约束实际查自哪个 `provider/model`；未知为 null。
   *
   * 传入的后端可能是裸 provider（`video_backend: "gemini-aistudio"`，服务端会补全默认视频模型）
   * 或留空跟随全局默认，此时该值取服务端解析结果。凡按「项目为该后端保存了什么」查项目配置的
   * 调用点都须用它，否则裸 provider 会被当成 model ID 去查 `model_settings`、读不到实际档位。
   */
  resolvedVideoBackend: string | null;
  /** 首帧 / 尾帧生效值（含用户覆盖）；尚未查到或查询失败时为 null（未知），不谎报不支持。 */
  firstFrame: boolean | null;
  lastFrame: boolean | null;
  /**
   * 声音一致性三级标识（服务端二维派生：模型能力 × 项目 generation_mode），与 firstFrame 同源
   * 同口径——尚未查到或查询失败时为 null（未知）。查询带上 videoBackend，故编辑中的未保存候选
   * 模型也会得到它自己的档位。无项目上下文（全局设置页）时改读目录端点的同名字段。
   */
  voiceConsistency: VoiceConsistencyTier | null;
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
 * 用 hook 已取到的能力，对另一份联动约束上下文再算一次收窄结果；未知为 null。
 *
 * 供上下文按集变化、而能力查询只能在组件顶层做一次的调用点使用——`rawDurations` 与
 * `durationConstraints` 不随上下文变化，收窄规则仍收在本模块内，不在调用点重新拼一条查表链路。
 */
export function narrowDurations(
  caps: Pick<ModelCapabilities, "rawDurations" | "durationConstraints">,
  ctx: DurationContext,
): number[] | null {
  return caps.rawDurations ? narrow(caps.rawDurations, caps.durationConstraints, ctx) : null;
}

/**
 * 已保存时长越界的成因；不越界为 null。
 *
 * 成因决定提示该把用户引向哪里：`model` 只能换时长或换模型，`resolution` / `reference` 则是
 * 改对应设置也能解决。判定顺序即优先级——全集就不含该值时，联动约束是不是也排除它无关紧要。
 */
export type DurationOutOfRangeReason = "model" | "reference" | "resolution";

export function durationOutOfRangeReason(
  saved: number | null | undefined,
  caps: Pick<ModelCapabilities, "rawDurations" | "supportedDurations" | "durationConstraints">,
  ctx: DurationContext = {},
): DurationOutOfRangeReason | null {
  const { rawDurations, supportedDurations, durationConstraints } = caps;
  if (saved == null || !supportedDurations || supportedDurations.includes(saved)) return null;
  if (!rawDurations?.includes(saved)) return "model";
  const { withReferenceImages } = durationConstraints;
  if (ctx.usesReferenceImages && withReferenceImages.length > 0 && !withReferenceImages.includes(saved))
    return "reference";
  return "resolution";
}

/**
 * 该集生效 generation_mode；无集号上下文（设置页等）或项目数据未加载时为 null。
 *
 * 只参与请求 key，不进请求参数——服务端已按集号自行解析。集级覆盖经
 * `PATCH /projects/{name}` 写入、再由项目事件 SSE 刷进 store，工作台挂载期间就会变；
 * 而集号本身不变，key 不含模式的话该覆盖变了也不重取，能力停在上一模式。
 */
function useEpisodeGenerationMode(
  projectName: string | undefined | null,
  episode: number | undefined | null,
): GenerationMode | null {
  const project = useProjectsStore((s) =>
    projectName && s.currentProjectName === projectName ? s.currentProjectData : null,
  );
  if (episode === null || episode === undefined || !project) return null;
  return effectiveMode(project, project.episodes?.find((e) => e.episode === episode));
}

/**
 * 服务端能力查询。
 *
 * 请求 key 只含「决定结果是否仍可用」的上下文：项目、后端、集号与该集生效模式。这几项一变
 * 必须立刻丢弃旧能力，避免按过期值门控；revision 则单独驱动重取而不进 key——能力覆盖改的是
 * 供应商配置，旧值在新值到达前仍是当前最优估计，进 key 会让每次覆盖变更都闪一次加载态、
 * 短暂灰掉控件。
 *
 * 加载态由「已落地结果的 key 是否等于当前 key」派生，而非 effect 内同步 setState：
 * 后者会触发级联渲染（react-hooks/set-state-in-effect）。
 */
function useResolvedCapabilities(
  projectName: string | undefined | null,
  videoBackend: string | undefined | null,
  episode: number | undefined | null,
  enabled: boolean,
): { caps: VideoCapabilities | null; loading: boolean } {
  const revision = useCapabilitiesStore((s) => s.revision);
  const [result, setResult] = useState<{ key: string; caps: VideoCapabilities | null } | null>(
    null,
  );
  const abortRef = useRef<AbortController | null>(null);

  // 演示项目后端不存在，直接返回空能力而不发请求。
  const active = enabled && !!projectName && !isDemoProject(projectName);
  // 元组编码而非拼接：拼接的分隔符可能出现在字段内，("a b", "c") 与 ("a", "b c") 会撞成同一 key，
  // 切换时把前一组的结果当作本组已落地。
  const episodeMode = useEpisodeGenerationMode(projectName, episode);
  const key = active
    ? JSON.stringify([projectName, videoBackend ?? "", episode ?? null, episodeMode])
    : null;

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
    // 带上 videoBackend：表单里编辑中的候选模型也要拿到它自己的能力，否则服务端按已落盘
    // 配置解析，档位等二维派生值会停留在上一次保存的模型上。
    API.getVideoCapabilities(projectName, {
      signal,
      videoBackend: videoBackend || undefined,
      episode: episode ?? undefined,
    })
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
    // videoBackend / episode 已编码进 key，列出只为满足 exhaustive-deps，不引入额外请求。
  }, [key, projectName, videoBackend, episode, revision]);

  const settled = key !== null && result?.key === key;
  return { caps: settled ? result.caps : null, loading: key !== null && !settled };
}

export function useModelCapabilities({
  projectName,
  videoBackend,
  unsavedBackend = false,
  episode,
  providers = EMPTY_PROVIDERS,
  customProviders = EMPTY_CUSTOM_PROVIDERS,
  videoResolution,
  usesReferenceImages,
  enabled = true,
}: ModelCapabilitiesInput): ModelCapabilities {
  const { caps, loading } = useResolvedCapabilities(projectName, videoBackend, episode, enabled);

  // 时长连同它出自哪个模型一起解析：联动约束要按同一个模型查，否则走服务端回退时会拿传入的
  // 后端（留空，或已不在目录中的存值）去查约束，得到空约束、把未收窄的时长当作可选项。
  const durationSource = useMemo<{ raw: number[]; backend: string } | null>(() => {
    if (!enabled) return null;
    if (videoBackend) {
      const fromCatalog = lookupSupportedDurations(providers, videoBackend, customProviders);
      if (fromCatalog?.length) return { raw: fromCatalog, backend: videoBackend };
    }
    if (unsavedBackend) return null;
    if (!caps?.supported_durations?.length) return null;
    return { raw: caps.supported_durations, backend: `${caps.provider_id}/${caps.model}` };
  }, [enabled, providers, customProviders, videoBackend, unsavedBackend, caps]);

  const rawDurations = durationSource?.raw ?? null;

  const durationConstraints = useMemo(
    () =>
      durationSource ? lookupDurationConstraints(providers, durationSource.backend) : EMPTY_CONSTRAINTS,
    [providers, durationSource],
  );

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
    resolvedVideoBackend: durationSource?.backend ?? null,
    firstFrame: caps ? caps.first_frame : null,
    lastFrame: caps ? caps.last_frame : null,
    voiceConsistency: caps ? caps.voice_consistency : null,
    loading,
  };
}
