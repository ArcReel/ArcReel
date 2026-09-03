import { API } from "@/api";
import type {
  CustomProviderInfo,
  MediaType,
  ProviderInfo,
  VideoAudioControl,
  VideoRoute,
  VoiceConsistencyTier,
} from "@/types";

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

/** 目录里的供应商名与模型名（"provider/model" 为键），均已按 Accept-Language 成文。 */
export interface CatalogDisplayNames {
  providerNames: Record<string, string>;
  modelNames: Record<string, string>;
}

/**
 * 从目录（`/providers` 与 `/custom-providers`）抽出显示名表，作为候选响应之外的兜底层。
 *
 * 两个端点都不按可用性收窄，候选与整页配置则只列 ready 的内置供应商与已启用的自定义模型；
 * 而生效值可以指向一个已失去凭证的内置供应商，或一个被停用的自定义模型（停用不清引用）。
 * 那时下拉触发按钮仍要显示名字，而不是 `custom-3 · my-model` 这样的裸 id。
 */
export function catalogDisplayNames(
  providers: ProviderInfo[],
  customProviders: CustomProviderInfo[] = [],
): CatalogDisplayNames {
  const providerNames: Record<string, string> = {};
  const modelNames: Record<string, string> = {};
  for (const provider of providers) {
    providerNames[provider.id] = provider.display_name;
    for (const [modelId, model] of Object.entries(provider.models ?? {})) {
      modelNames[`${provider.id}/${modelId}`] = model.display_name;
    }
  }
  for (const provider of customProviders) {
    const providerId = `${CUSTOM_PREFIX}${provider.id}`;
    providerNames[providerId] = provider.display_name;
    for (const model of provider.models ?? []) {
      modelNames[`${providerId}/${model.model_id}`] = model.display_name;
    }
  }
  return { providerNames, modelNames };
}

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

/**
 * 目录里该模型的时长全集，升序；目录查不到为 null。
 *
 * 这是型号声明、不经任何联动约束收窄的集合，供「展示模型规格」与「切换模型时判断已选时长
 * 是否仍属于新模型」这类同步场景使用。按当前分辨率 / 参考图路径收窄后的可选集只由服务端
 * video-capabilities 端点回答（`useModelCapabilities`），前端不复算收窄规则。
 */
export function catalogDurations(
  providers: ProviderInfo[],
  customProviders: CustomProviderInfo[],
  videoBackend: string,
): number[] | null {
  if (!videoBackend) return null;
  const raw = lookupSupportedDurations(providers, videoBackend, customProviders);
  return raw?.length ? [...raw].sort((a, b) => a - b) : null;
}

/** 目录（非自定义供应商）里的视频音轨能力：音轨是否存在 + 服务端派生的声音一致性档位。 */
export interface CatalogVideoAudio {
  hasAudioTrack: boolean;
  /** 无项目上下文下的档位，服务端派生。有项目上下文时改用能力查询结果，不读此值。 */
  voiceConsistency: VoiceConsistencyTier;
}

/**
 * 给定 "provider/model"，查目录里的音频相关声明——下拉能力线（音轨）与全局设置页的档位
 * 徽章共用同一份查表。自定义供应商目录无逐模型声明，与服务端 `_resolve_video_caps_for_model`
 * 同口径固定假定有声（soft）——无信号时判定为有声但保证降级，比误判为无声更不易误导。
 */
export function lookupCatalogVideoAudio(
  providers: ProviderInfo[],
  videoBackend: string,
): CatalogVideoAudio | null {
  const slashIdx = videoBackend.indexOf("/");
  if (slashIdx === -1) return null;
  const providerId = videoBackend.slice(0, slashIdx);
  const modelId = videoBackend.slice(slashIdx + 1);

  if (providerId.startsWith(CUSTOM_PREFIX)) return { hasAudioTrack: true, voiceConsistency: "soft" };

  const provider = providers.find((p) => p.id === providerId);
  const model = provider?.models?.[modelId];
  if (!model) return null;
  return { hasAudioTrack: model.audio_track !== "always_off", voiceConsistency: model.voice_consistency };
}

/**
 * 查该 `provider/model` 在某条执行路径上的音频开关可控性；查不到模型返回 null（调用方按可控
 * 处理，不收紧）。
 *
 * 三态由服务端从 backend 的 VideoCapabilities 派生，前端只按路径取对应字段，不解读
 * capabilities token、也不自行合成三态。恒有声与恒无声的成片音轨不随开关变化，设置界面据此
 * 禁用开关——否则用户的关闭意图到不了供应商，却会让编排层按无声裁掉全部音色约束。
 *
 * `route` 必传：同一 model 的两条子路径可以给出不同答案（可灵 v3-omni 图生可控、参考生无
 * 开关），按无路径上下文的值渲染会让参考生视频的用户开了音频却拿到无声成片。
 *
 * 自定义供应商目录无逐模型音轨声明，与服务端 `_resolve_video_caps_for_model` 同口径按「无
 * 信号不收紧」处理，保持开关可控。
 */
export function lookupVideoAudioControl(
  providers: ProviderInfo[],
  videoBackend: string,
  route: VideoRoute,
): VideoAudioControl | null {
  const slashIdx = videoBackend.indexOf("/");
  if (slashIdx === -1) return null;
  const providerId = videoBackend.slice(0, slashIdx);
  if (providerId.startsWith(CUSTOM_PREFIX)) return "controllable";

  const provider = providers.find((p) => p.id === providerId);
  const model = provider?.models?.[videoBackend.slice(slashIdx + 1)];
  if (!model) return null;
  return route === "r2v" ? model.reference_route_audio_track : model.audio_track;
}

// ---------------------------------------------------------------------------
// Resolution lookup
// ---------------------------------------------------------------------------

export const IMAGE_STANDARD_RESOLUTIONS = ["512px", "1K", "2K", "4K"];
export const VIDEO_STANDARD_RESOLUTIONS = ["480p", "720p", "1080p", "4K"];

/** 返回该 (provider, model) 下的分辨率候选 + 是否自定义供应商（决定 picker 模式）。
 *  自定义供应商路径需要从 endpoint 推 media_type 选标准分辨率集；该 map 由调用方
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
