import { API } from "@/api";
import type { CustomProviderInfo, MediaType, ProviderInfo } from "@/types";

const CUSTOM_PREFIX = "custom-";

// ---------------------------------------------------------------------------
// Provider fetchers
//
// 供应商配置可变（用户在设置页改模型 supported_durations / 启用状态等），前端不持久缓存它：
// 每次消费都直拉后端，避免长生命周期副本与后端单一真相源漂移（ADR 0035，ADR 0018/0013 的前端推论）。
// ---------------------------------------------------------------------------

/** Fetch the built-in provider list (including models) fresh on every call. */
export async function getProviderModels(): Promise<ProviderInfo[]> {
  const res = await API.getProviders();
  return res.providers;
}

/** Fetch the custom provider list fresh on every call. */
export async function getCustomProviderModels(): Promise<CustomProviderInfo[]> {
  const res = await API.listCustomProviders();
  return res.providers;
}

// ---------------------------------------------------------------------------
// Lookup
// ---------------------------------------------------------------------------

/**
 * Given a video backend string like "gemini-aistudio/veo-3.1-generate-preview"
 * or "custom-3/my-model", look up supported_durations.
 * Returns undefined if provider/model not found.
 */
export function lookupSupportedDurations(
  providers: ProviderInfo[],
  videoBackend: string,
  customProviders?: CustomProviderInfo[],
): number[] | undefined {
  const slashIdx = videoBackend.indexOf("/");
  if (slashIdx === -1) return undefined;
  const providerId = videoBackend.slice(0, slashIdx);
  const modelId = videoBackend.slice(slashIdx + 1);

  // Custom provider: "custom-{db_id}/{model_id}"
  if (providerId.startsWith(CUSTOM_PREFIX) && customProviders) {
    const dbId = parseInt(providerId.slice(CUSTOM_PREFIX.length), 10);
    const cp = customProviders.find((p) => p.id === dbId);
    const model = cp?.models?.find((m) => m.model_id === modelId);
    if (model?.supported_durations?.length) {
      return model.supported_durations;
    }
    return undefined;
  }

  // Built-in provider
  const provider = providers.find((p) => p.id === providerId);
  const model = provider?.models?.[modelId];
  return model?.supported_durations?.length
    ? model.supported_durations
    : undefined;
}

// ---------------------------------------------------------------------------
// Duration constraints
//
// 时长并非只由 supported_durations 决定：部分模型（当前是 Veo 全系）在高分辨率或参考图
// 路径下把时长收窄到单一取值。约束声明在后端 registry 的 ModelInfo 上，这里只做消费——
// UI 据此收窄可选项，用户就不会选到入队后必然被拒的组合。
// ---------------------------------------------------------------------------

export interface DurationConstraints {
  /** 分辨率（小写）→ 该分辨率下允许的时长 */
  byResolution: Record<string, number[]>;
  /** 使用参考图时允许的时长；空数组 = 无额外约束 */
  withReferenceImages: number[];
}

const EMPTY_CONSTRAINTS: DurationConstraints = { byResolution: {}, withReferenceImages: [] };

/** 读取该 (provider, model) 的时长联动约束。自定义供应商不表达这类约束，返回空。 */
export function lookupDurationConstraints(
  providers: ProviderInfo[],
  videoBackend: string,
): DurationConstraints {
  const slashIdx = videoBackend.indexOf("/");
  if (slashIdx === -1) return EMPTY_CONSTRAINTS;
  const providerId = videoBackend.slice(0, slashIdx);
  if (providerId.startsWith(CUSTOM_PREFIX)) return EMPTY_CONSTRAINTS;

  const provider = providers.find((p) => p.id === providerId);
  const model = provider?.models?.[videoBackend.slice(slashIdx + 1)];
  if (!model) return EMPTY_CONSTRAINTS;

  const byResolution: Record<string, number[]> = {};
  for (const [resolution, durations] of Object.entries(
    model.duration_resolution_constraints ?? {},
  )) {
    byResolution[resolution.toLowerCase()] = durations;
  }
  return { byResolution, withReferenceImages: model.reference_image_durations ?? [] };
}

/**
 * 按当前分辨率与是否走参考图路径收窄时长候选。
 *
 * 两条约束各自独立触发、可同时生效，取交集。交集为空说明声明之间自相矛盾（不该发生），
 * 此时保留原候选而非清空——留一个空的时长选择器会让用户无从操作，且后端仍有执行期拦截。
 */
export function constrainDurations(
  durations: readonly number[],
  constraints: DurationConstraints,
  ctx: { resolution?: string | null; usesReferenceImages?: boolean },
): number[] {
  let allowed = [...durations];
  if (ctx.usesReferenceImages && constraints.withReferenceImages.length > 0) {
    allowed = allowed.filter((d) => constraints.withReferenceImages.includes(d));
  }
  const byResolution = ctx.resolution
    ? constraints.byResolution[ctx.resolution.toLowerCase()]
    : undefined;
  if (byResolution?.length) {
    allowed = allowed.filter((d) => byResolution.includes(d));
  }
  return allowed.length > 0 ? allowed : [...durations];
}

// ---------------------------------------------------------------------------
// Resolution lookup
// ---------------------------------------------------------------------------

export const IMAGE_STANDARD_RESOLUTIONS = ["512px", "1K", "2K", "4K"];
export const VIDEO_STANDARD_RESOLUTIONS = ["480p", "720p", "1080p", "4K"];

/** 返回该 (provider, model) 下的分辨率候选 + 是否自定义供应商（决定 picker 模式）。
 *  自定义 provider 路径需要从 endpoint 推 media_type 选标准分辨率集；该 map 由调用方
 *  从 endpoint-catalog-store 读出注入（保持本文件无 store 副作用）。 */
export function lookupResolutions(
  providers: ProviderInfo[],
  backend: string,
  customProviders?: CustomProviderInfo[],
  endpointToMediaType?: Record<string, MediaType>,
): { options: string[]; isCustom: boolean } {
  const slashIdx = backend.indexOf("/");
  if (slashIdx === -1) return { options: [], isCustom: false };
  const providerId = backend.slice(0, slashIdx);
  const modelId = backend.slice(slashIdx + 1);

  if (providerId.startsWith(CUSTOM_PREFIX) && customProviders) {
    const dbId = parseInt(providerId.slice(CUSTOM_PREFIX.length), 10);
    const cp = customProviders.find((p) => p.id === dbId);
    const model = cp?.models?.find((m) => m.model_id === modelId);
    if (!model) return { options: [], isCustom: true };
    const media = endpointToMediaType?.[model.endpoint];
    const standard =
      media === "image"
        ? IMAGE_STANDARD_RESOLUTIONS
        : media === "video"
          ? VIDEO_STANDARD_RESOLUTIONS
          : [];
    return { options: standard, isCustom: true };
  }

  const provider = providers.find((p) => p.id === providerId);
  const model = provider?.models?.[modelId];
  return { options: model?.resolutions ?? [], isCustom: false };
}
