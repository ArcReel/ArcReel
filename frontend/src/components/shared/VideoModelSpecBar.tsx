import { Mic, Quote, VolumeX } from "lucide-react";
import { cn } from "cn";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { formatDurationsLabel } from "@/utils/duration_format";
import {
  catalogDurations,
  lookupCatalogVideoAudio,
  lookupResolutions,
  lookupVideoAudioControl,
} from "@/utils/provider-models";
import type { CustomProviderInfo } from "@/types/custom-provider";
import type { MediaType, ProviderInfo, VideoRoute, VoiceConsistencyTier } from "@/types";

// ---------------------------------------------------------------------------
// 声音一致性档位元数据。
//
// 档位含义只在此处声明一次：颜色/图标/文案随档位联动，避免各调用点各拼一份判断。
// ---------------------------------------------------------------------------

const TIER_ICON: Record<VoiceConsistencyTier, typeof Mic> = {
  native: Mic,
  soft: Quote,
  none: VolumeX,
};

const TIER_CLASS: Record<VoiceConsistencyTier, string> = {
  native: "border-primary/25 bg-primary/10 text-primary",
  soft: "border-warn/35 bg-warn/10 text-warn",
  none: "border-border bg-foreground/5 text-muted-foreground",
};

/** 声音一致性档位徽章：图标 + 文案 + 悬停说明。 */
export function VoiceConsistencyBadge({ tier }: { tier: VoiceConsistencyTier }) {
  const { t } = useTranslation("dashboard");
  const Icon = TIER_ICON[tier];
  const label = t(`voice_consistency_${tier}_label`);
  const desc = t(`voice_consistency_${tier}_desc`);
  return (
    // 降级口径只挂 title 的话读屏用户读不到；补 role="note" + aria-label 让它进无障碍树。
    <span
      role="note"
      title={desc}
      aria-label={`${label}: ${desc}`}
      className={cn("inline-flex w-fit cursor-help items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs font-medium", TIER_CLASS[tier])}
    >
      <Icon className="size-3" aria-hidden />
      {label}
    </span>
  );
}

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

export interface VideoModelSpecBarProps {
  /** 未收窄的模型原生时长全集；未知为 null。 */
  durations: number[] | null;
  resolutions: string[];
  /** 声音一致性档位；未知（能力未查到）为 null。音轨格同源于此——tier !== "none" 即有声。 */
  tier: VoiceConsistencyTier | null;
}

/** 选择器下方的规格条：时长 / 分辨率 / 音轨 / 声音一致性，四格集中展示当前选中模型的能力。 */
export function VideoModelSpecBar({ durations, resolutions, tier }: VideoModelSpecBarProps) {
  const { t } = useTranslation("dashboard");

  const cells: { label: string; content: React.ReactNode }[] = [
    {
      // 规格条展示的是模型支持的时长全集，不是 templates:duration_label（「默认时长」，用户偏好）。
      label: t("video_spec_duration_label"),
      content:
        durations && durations.length > 0 ? (
          <span className="text-xs tabular-nums text-subtle-foreground">
            {formatDurationsLabel(durations)}
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        ),
    },
    {
      label: t("resolution_label"),
      content:
        resolutions.length > 0 ? (
          <span className="text-xs text-subtle-foreground">{resolutions.join(" / ")}</span>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        ),
    },
    {
      label: t("video_spec_audio_label"),
      content:
        tier !== null ? (
          <span className="text-xs text-subtle-foreground">
            {t(tier === "none" ? "video_spec_audio_none" : "video_spec_audio_has")}
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        ),
    },
    {
      label: t("voice_consistency_label"),
      content: tier !== null ? <VoiceConsistencyBadge tier={tier} /> : <span className="text-xs text-muted-foreground">—</span>,
    },
  ];

  // 两列还是四列取决于规格条自身的宽度：它会出现在全局设置、项目设置与新建项目向导里。
  return (
    <div className="@container">
      <div className="grid grid-cols-2 gap-y-3 rounded-md border border-border bg-muted/40 px-3 py-2.5 @md:grid-cols-4 @md:gap-y-0">
        {cells.map((c, i) => (
          <div
            key={c.label}
            className={cn("flex flex-col gap-1 pl-3 first:pl-0", i > 0 && "@md:border-l @md:border-border")}
          >
            <span className="text-xs text-muted-foreground">{c.label}</span>
            {c.content}
          </div>
        ))}
      </div>
    </div>
  );
}
