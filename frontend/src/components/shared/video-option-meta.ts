import type { TFunction } from "i18next";
import { formatDurationsLabel } from "@/utils/duration_format";
import { catalogDurations, lookupCatalogVideoAudio, lookupResolutions, lookupVideoAudioControl } from "@/utils/provider-models";
import type { CustomProviderInfo } from "@/types/custom-provider";
import type { MediaType, ProviderInfo, VideoRoute } from "@/types";

/**
 * 下拉选项行的能力线渲染器（时长 / 分辨率 / 音轨），交给 `ProviderModelSelect.renderOptionMeta`。
 * 项目设置页与全局设置页同一份拼装：两处只有 `endpointToMediaType` 有无之别，不各写一份。
 *
 * 刻意不是 hook——调用方可能在早退分支之后才构造渲染器，内部再调 `useTranslation` 会违反
 * hooks 调用顺序规则；`t` 由调用方传入，key 一律带 `dashboard:` 前缀以免受调用方默认命名空间影响。
 */
export function videoOptionMetaRenderer({
  t,
  providers,
  customProviders,
  endpointToMediaType,
  defaultRoute,
}: {
  t: TFunction;
  providers: ProviderInfo[];
  customProviders: CustomProviderInfo[];
  endpointToMediaType?: Record<string, MediaType>;
  /** 默认层下拉的执行路径。默认层跨全部用途，路径只能由调用方的上下文给出（项目是否走参考生
   *  视频）；全局设置页无项目上下文，省略即回退目录 i2v 位。细分项下拉不看此值——它们各自的
   *  `key` 就是路径，见下方 `asVideoRoute`。 */
  defaultRoute?: VideoRoute;
}) {
  return (fullValue: string, subFieldKey?: string) => {
    // 细分项下拉自带路径，默认层才回落到调用方给的路径：同屏三个视频下拉分属不同路径，
    // 共用一条会让「图生视频」那一格按参考生的口径标注（或反之）。
    const route = asVideoRoute(subFieldKey) ?? defaultRoute;
    const durations = catalogDurations(providers, customProviders, fullValue);
    const resolutions = lookupResolutions(
      providers,
      fullValue,
      customProviders,
      endpointToMediaType,
    ).options;
    // 查不到模型时为 null，音轨格整格不渲染——两条分支都不臆造一个答案。
    const audioControl = route ? lookupVideoAudioControl(providers, fullValue, route) : null;
    const hasAudioTrack = route
      ? (audioControl === null ? null : audioControl !== "always_off")
      : (lookupCatalogVideoAudio(providers, fullValue)?.hasAudioTrack ?? null);
    const parts: string[] = [];
    if (durations?.length) parts.push(formatDurationsLabel(durations));
    if (resolutions.length) parts.push(resolutions.join(" / "));
    if (hasAudioTrack !== null) {
      parts.push(t(hasAudioTrack ? "dashboard:video_spec_audio_has" : "dashboard:video_spec_audio_none"));
    }
    return parts.length > 0 ? parts.join(" · ") : t("dashboard:video_option_caps_unknown");
  };
}

/**
 * 细分项 `key` 中的视频执行路径。`LayeredModelFields` 的细分项按任务类型桶命名，视频两个桶
 * 恰好就是两条路径；图片桶（t2i / i2i）与文本档位不是视频路径，返回 null 交由默认层路径兜底。
 */
function asVideoRoute(subFieldKey: string | undefined): VideoRoute | null {
  return subFieldKey === "i2v" || subFieldKey === "r2v" ? subFieldKey : null;
}
