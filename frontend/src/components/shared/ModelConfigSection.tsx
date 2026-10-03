import { useEffect, useMemo } from "react";
import { cn } from "cn";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { InlineWarning } from "@/components/shared/InlineWarning";
import {
  durationOutOfRangeReason,
  useModelCapabilities,
} from "@/hooks/useModelCapabilities";
import {
  catalogDurations,
  lookupCatalogVideoAudio,
  lookupEndpointConstraints,
  lookupResolutions,
  lookupVideoAudioControl,
  resolutionPlaceholder,
} from "@/utils/provider-models";
import { isContinuousIntegerRange } from "@/utils/duration_format";
import { ResolutionPicker } from "./ResolutionPicker";
import {
  LayeredModelFields,
  degradeSubFieldsToSaved,
  effectiveModel,
  executingImageModel,
  executingVideoModel,
  useGenerationTypeBucketLabels,
  type LayeredSubField,
} from "./LayeredModelFields";
import { TextTierFields } from "./TextTierFields";
import { VideoModelSpecBar, videoOptionMetaRenderer } from "./VideoModelSpecBar";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type { ProviderInfo, VoiceConsistencyTier } from "@/types/provider";
import type { CustomProviderInfo } from "@/types/custom-provider";
import type { ModelCandidatesResponse } from "@/types/system";

const EMPTY_CUSTOM_PROVIDERS: CustomProviderInfo[] = [];

export interface ModelConfigValue {
  /** 项目默认视频模型；两个视频细分项留空时回退到它，再回退全局层。 */
  videoBackend: string;
  videoProviderI2V: string;
  videoProviderR2V: string;
  /** 项目默认图片模型；两个图片细分项留空时回退到它，再回退全局层。 */
  imageBackendDefault: string;
  imageBackendT2I: string;
  imageBackendI2I: string;
  textBackendDefault: string;
  textBackendSimple: string;
  textBackendComplex: string;
  defaultDuration: number | null;
  videoResolution: string | null;
  videoResolutions?: Record<string, string | null>;
  imageResolution: string | null;
}

export interface ModelConfigSectionProps {
  value: ModelConfigValue;
  onChange: (next: ModelConfigValue) => void;
  /**
   * 所属项目名。项目已存在时（设置页）传入；创建向导的项目尚不存在，
   * 省略时能力管线按候选模型走无项目端点。
   */
  projectName?: string | null;
  options: {
    videoBackends: string[];
    imageBackends: string[];
    textBackends: string[];
    providerNames: Record<string, string>;
    /** "provider/model" → 当前语言下的模型名，与 `providerNames` 同源；缺键退回 model id。 */
    modelNames?: Record<string, string>;
  };
  /**
   * 是否呈现「按用途指定模型」这一层（媒体细分下拉与文本档位）。false 即只留默认层，
   * 创建向导用它。与 candidates 分工：这里表达意图，candidates 只提供媒体细分的候选数据，
   * 后者拉取失败时文本档位不受影响。
   */
  showSubFields?: boolean;
  /**
   * 媒体细分项的候选（docs/adr/0054）。缺席（拉取失败）时细分区降级：只保留已配置的细分项、
   * 候选只列其当前值，未配置项不渲染；全部未配置即整块折叠区不渲染。
   */
  candidates?: ModelCandidatesResponse | null;
  /**
   * 候选拉取的失败态与重试入口；非空时视频 / 图片两处折叠区各渲染一条错误提示。与
   * `candidates` 缺席但未失败（仍在加载中）区分——后者沿用上面的静默降级，不显示错误态。
   * 文本档位不取用候选数据，不参与。
   */
  candidatesError?: { onRetry: () => void; retrying?: boolean };
  providers: ProviderInfo[];
  customProviders?: CustomProviderInfo[];
  /** 全局各层的已配置值（原样传入、不在调用处折叠回退），穿透演算按解析链就地推导。 */
  globalDefaults: {
    video: string;
    videoI2V: string;
    videoR2V: string;
    image: string;
    imageT2I: string;
    imageI2I: string;
    textDefault: string;
    textSimple: string;
    textComplex: string;
  };
  /**
   * 项目级「生成有声视频」覆盖（null=跟随全局，true/false=显式覆盖）。
   * 仅在传入 onVideoGenerateAudioChange 时于视频通道内渲染该开关——此项是视频模型的能力开关，
   * 与旁白配音（TTS）无关，故归在视频通道而非单列。创建项目向导不传则不渲染。
   */
  videoGenerateAudio?: boolean | null;
  /**
   * 全局「生成有声视频」的生效值，用于把 `videoGenerateAudio` 的 null（跟随全局）折叠成实际
   * 生效值——矛盾提示要按生效值给，否则项目留空而全局为「关闭」时界面无从察觉。省略即按开启处理。
   */
  globalVideoGenerateAudio?: boolean;
  onVideoGenerateAudioChange?: (next: boolean | null) => void;
  /**
   * 当前项目是否走参考生视频（资产图直出）。部分模型在参考图路径下把时长收窄到单一取值，
   * 时长选项据此过滤——该模式由所在页面持有，故从外部传入而非在本组件推断。
   */
  usesReferenceImages?: boolean;
  enable?: {
    video?: boolean;
    image?: boolean;
    text?: boolean;
    duration?: boolean;
  };
}

function ChannelCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <h3 className="text-sm font-medium">{title}</h3>
      {children}
    </section>
  );
}

/** 通道内的字段小标题：本地化常规字，不用等宽大写。 */
function FieldTitle({ children }: { children: React.ReactNode }) {
  return <div className="text-xs font-medium text-muted-foreground">{children}</div>;
}

export function ModelConfigSection({
  value,
  onChange,
  projectName,
  options,
  showSubFields = true,
  candidates,
  candidatesError,
  providers,
  customProviders = EMPTY_CUSTOM_PROVIDERS,
  globalDefaults,
  videoGenerateAudio,
  globalVideoGenerateAudio = true,
  onVideoGenerateAudioChange,
  usesReferenceImages,
  enable,
}: ModelConfigSectionProps) {
  const { t } = useTranslation(["templates", "dashboard"]);

  const endpointToMediaType = useEndpointCatalogStore((s) => s.endpointToMediaType);
  const endpointConstraints = useEndpointCatalogStore((s) => s.endpointConstraints);
  const fetchEndpointCatalog = useEndpointCatalogStore((s) => s.fetch);
  useEffect(() => {
    if (customProviders.length > 0) void fetchEndpointCatalog();
  }, [customProviders.length, fetchEndpointCatalog]);

  const showVideo = enable?.video !== false;
  const showImage = enable?.image !== false;
  const showText = enable?.text !== false;
  const showDuration = enable?.duration !== false;

  const bucketLabels = useGenerationTypeBucketLabels();

  // 时长 / 分辨率 / 声音档位按模型查能力，问的必须是当前配置真正会执行的模型：细分项被覆盖时
  // 它不是默认层那个模型，拿默认层去查会把用户引到执行时并不支持的时长与分辨率上。
  const executingVideo = executingVideoModel(value, globalDefaults, usesReferenceImages);
  const executingI2V = executingVideoModel(value, globalDefaults, false);
  const executingImage = executingImageModel(value, globalDefaults);

  // 穿透演算（docs/adr/0054，项目优先）：细分项留空 → 项目默认模型 → 全局同名细分 → 全局默认模型。
  const mediaCandidates = showSubFields ? candidates : null;
  const mediaCandidatesError = showSubFields ? candidatesError : undefined;

  // 时长与分辨率都按执行模型取值，故默认层与细分项的任何一次改动都先看执行模型变没变：
  // 没变（细分项已覆盖时改默认层就是这种）原样写入，变了才清掉不再适用的分辨率、并把落在
  // 新模型声明全集之外的时长退回自动。两条路径共用一处，避免只有主下拉做校验、细分项漏做。
  // 全集在目录里同步可得；新模型走参考图路径时若把该值收窄掉，由下方按成因的提示引导重选，
  // 事件处理器里拿不到服务端的收窄结果。
  const applyVideoLayer = (patch: Partial<ModelConfigValue>) => {
    const next = { ...value, ...patch };
    const nextExecuting = executingVideoModel(next, globalDefaults, usesReferenceImages);
    if (nextExecuting === executingVideo) {
      onChange(next);
      return;
    }
    const nextDurations = catalogDurations(providers, customProviders, nextExecuting);
    const keepDuration = next.defaultDuration !== null && !!nextDurations?.includes(next.defaultDuration);
    onChange({
      ...next,
      defaultDuration: keepDuration ? next.defaultDuration : null,
      videoResolution: value.videoResolutions ? (value.videoResolutions[nextExecuting] ?? null) : null,
    });
  };

  const applyImageLayer = (patch: Partial<ModelConfigValue>) => {
    const next = { ...value, ...patch };
    const nextExecuting = executingImageModel(next, globalDefaults);
    onChange(nextExecuting === executingImage ? next : { ...next, imageResolution: null });
  };

  const videoSubFields: LayeredSubField[] | undefined = showSubFields
    ? degradeSubFieldsToSaved([
        {
          key: "i2v",
          ...bucketLabels.i2v,
          value: value.videoProviderI2V,
          options: mediaCandidates?.video.buckets.i2v ?? [],
          effective: effectiveModel(value.videoBackend, globalDefaults.videoI2V, globalDefaults.video),
          onChange: (next: string) => applyVideoLayer({ videoProviderI2V: next }),
        },
        {
          key: "r2v",
          ...bucketLabels.r2v,
          value: value.videoProviderR2V,
          options: mediaCandidates?.video.buckets.r2v ?? [],
          effective: effectiveModel(value.videoBackend, globalDefaults.videoR2V, globalDefaults.video),
          onChange: (next: string) => applyVideoLayer({ videoProviderR2V: next }),
        },
      ], !!mediaCandidates)
    : undefined;

  const imageSubFields: LayeredSubField[] | undefined = showSubFields
    ? degradeSubFieldsToSaved([
        {
          key: "t2i",
          ...bucketLabels.t2i,
          value: value.imageBackendT2I,
          options: mediaCandidates?.image.buckets.t2i ?? [],
          effective: effectiveModel(value.imageBackendDefault, globalDefaults.imageT2I, globalDefaults.image),
          onChange: (next: string) => applyImageLayer({ imageBackendT2I: next }),
        },
        {
          key: "i2i",
          ...bucketLabels.i2i,
          value: value.imageBackendI2I,
          options: mediaCandidates?.image.buckets.i2i ?? [],
          effective: effectiveModel(value.imageBackendDefault, globalDefaults.imageI2I, globalDefaults.image),
          onChange: (next: string) => applyImageLayer({ imageBackendI2I: next }),
        },
      ], !!mediaCandidates)
    : undefined;

  // 能力统一经 useModelCapabilities 取得（见该模块的真相源规则），本组件不自行查表。
  // 本组件是表单：候选模型与分辨率都是编辑中的未保存值，显式带给服务端按它们求值；分辨率为
  // null 即「自动」，服务端不回退到已保存档位。无项目（创建向导）时走无项目端点。
  const capabilities = useModelCapabilities({
    projectName,
    videoBackend: executingVideo,
    videoResolution: value.videoResolution,
    usesReferenceImages,
  });
  const i2vCapabilities = useModelCapabilities({
    projectName,
    videoBackend: executingI2V,
    videoResolution: value.videoResolutions?.[executingI2V] ?? null,
    usesReferenceImages: false,
    enabled: !!usesReferenceImages && !!value.videoResolutions,
  });
  const { rawDurations, supportedDurations, voiceConsistency } = capabilities;
  // 约束上下文（分辨率 / 参考图路径）变了但模型没变时，旧的收窄结果会一直挂到新结果落地：
  // 这样切档位不闪加载态，但这段窗口里的选项属于上一个上下文。期间只展示、不接受选择，
  // 用户就不会从过期列表里挑一个新上下文并不允许的时长。
  const durationStale = capabilities.loading;

  // 声音一致性档位：有项目上下文时服务端按「候选模型 × 本项目 generation_mode」派生（能力查询
  // 已带上 videoBackend，故编辑中未保存的选择也对得上）；无项目上下文时读目录端点的同名字段，
  // 同样由服务端派生，前端两条路径都不含派生公式。
  const videoSpecTier: VoiceConsistencyTier | null = projectName
    ? voiceConsistency
    : (lookupCatalogVideoAudio(providers, executingVideo)?.voiceConsistency ?? null);

  // 音频开关按执行模型的可控性判定：恒有声 / 恒无声的模型收不到音轨开关，置灰并展示成片的
  // 实际音轨状态（而非存量配置值），存量的「关闭」由下方警告给一键修正入口，不静默改写配置。
  // 按路径取值而非按模型：可灵 v3-omni 图生可控、参考生无开关，只按模型取会让参考生视频放行一个
  // 执行期必然被丢弃的开关。
  const audioControl = lookupVideoAudioControl(providers, executingVideo, usesReferenceImages ? "r2v" : "i2v");
  const audioLocked = audioControl === "always_on" || audioControl === "always_off";
  const audioDisplayValue = audioLocked
    ? audioControl === "always_on"
    : (videoGenerateAudio ?? null);
  const audioLockedHint = audioLocked
    ? t(
        audioControl === "always_on"
          ? "dashboard:audio_switch_locked_always_on"
          : "dashboard:audio_switch_locked_always_off",
      )
    : null;
  // 按生效值判矛盾：项目级 null 表示跟随全局，全局为「关闭」时同样落在恒有声模型上。
  const audioConflict =
    audioControl === "always_on" && (videoGenerateAudio ?? globalVideoGenerateAudio) === false;

  // 该端点给不出任何时长档位时，档位空集是如实声明而非配置缺陷（docs/adr/0082）。判据取
  // durationTierEmpty 而非 durationFixed：frames 绑了却读不到帧率来源的那一支档位同样是空集，
  // 用户在项目页看到的结果一样是「这一维不由 ArcReel 驱动」。
  const videoDurationNotDriven =
    lookupEndpointConstraints(executingVideo, customProviders, endpointConstraints)?.durationTierEmpty ?? false;

  const videoResolutionOptions = lookupResolutions(
    providers,
    executingVideo,
    customProviders,
    endpointToMediaType,
  ).options;

  // 默认层下拉展示的是本项目实际会执行的那条路径（与上方 audioControl 同口径）；两个细分项
  // 下拉各按自己的桶取值，不受此处影响。
  const renderVideoOptionMeta = videoOptionMetaRenderer({
    t,
    providers,
    customProviders,
    endpointToMediaType,
    defaultRoute: usesReferenceImages ? "r2v" : "i2v",
  });

  const handleVideoChange = (next: string) => applyVideoLayer({ videoBackend: next });

  const handleImageChange = (next: string) => applyImageLayer({ imageBackendDefault: next });

  const handleDurationClick = (d: number | null) => {
    onChange({ ...value, defaultDuration: d });
  };

  // 已保存时长落在当前模型支持集之外（后端不变、支持集被外部缩小的挂载/重渲染场景）：
  // 不自动篡改存值，仅渲染提示并提供一键回退 auto，保留用户感知与重选机会。
  //
  // 提示文案按越界成因分开：模型全集就不含该值才是「模型不支持」，被联动约束收窄掉时说清
  // 是分辨率还是参考图路径——用户据此改对应设置，而不是被引去换模型。
  const durationNoticeKey = useMemo(() => {
    // 过期上下文算不出成因：此时不给提示，也不报“无问题”——两者都会误导，
    // 控件在同一窗口内不接受选择，新结果落地后提示按新上下文重算。
    if (durationStale) return null;
    const reason = durationOutOfRangeReason(value.defaultDuration, capabilities);
    if (reason === null) return null;
    return {
      model: "duration_unsupported_notice",
      reference: "duration_unsupported_reference_notice",
      resolution: "duration_unsupported_resolution_notice",
    }[reason];
  }, [value.defaultDuration, capabilities, durationStale]);

  const renderResolutionField = (
    backend: string,
    resolution: string | null,
    onResolutionChange: (v: string | null) => void,
    label = t("resolution_label"),
  ) => {
    const res = lookupResolutions(providers, backend, customProviders, endpointToMediaType);
    if (res.options.length === 0) return null;
    // 尺寸被 workflow 固定时选择器只展示不接受选择：比例与分辨率对该模型行无效，照收再丢弃
    // 只会让用户以为自己选的档位生效了。
    const constraints = lookupEndpointConstraints(backend, customProviders, endpointConstraints);
    const sizeFixed = constraints?.sizeFixed ?? false;
    return (
      <div className="flex flex-col gap-1">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-muted-foreground">{label}</span>
          <ResolutionPicker
            mode={res.isCustom ? "combobox" : "select"}
            options={res.options}
            value={resolution}
            onChange={onResolutionChange}
            placeholder={resolutionPlaceholder(constraints, t)}
            aria-label={label}
            disabled={sizeFixed}
          />
        </div>
        {/* 禁用原因必须有一行可见说明：title 对键盘与触屏不可达。 */}
        {sizeFixed && <p className="text-xs text-muted-foreground">{t("resolution_fixed_hint")}</p>}
      </div>
    );
  };

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">{t("default_hint")}</p>

      {showVideo && (
        <ChannelCard title={t("model_video")}>
          <LayeredModelFields
            defaultLabel={t("model_video_default")}
            defaultValue={value.videoBackend}
            defaultOptions={options.videoBackends}
            onDefaultChange={handleVideoChange}
            emptyLabel={t("use_global_default")}
            emptyHint={
              globalDefaults.video
                ? t("current_global_default", { value: globalDefaults.video })
                : undefined
            }
            defaultEffective={globalDefaults.video || undefined}
            providerNames={options.providerNames}
            modelNames={options.modelNames}
            renderOptionMeta={renderVideoOptionMeta}
            subFields={videoSubFields}
            subFieldsError={mediaCandidatesError}
          >
          {executingVideo && (
            <VideoModelSpecBar
              durations={rawDurations}
              resolutions={videoResolutionOptions}
              tier={videoSpecTier}
            />
          )}

          {usesReferenceImages && value.videoResolutions && renderResolutionField(
            executingI2V,
            value.videoResolutions[executingI2V] ?? null,
            (v) => onChange({ ...value, videoResolutions: { ...value.videoResolutions, [executingI2V]: v } }),
            `${bucketLabels.i2v.label} · ${t("resolution_label")}`,
          )}
          {usesReferenceImages && i2vCapabilities.videoModelError && (
            <InlineWarning message={i2vCapabilities.videoModelError} />
          )}
          {renderResolutionField(
            executingVideo,
            value.videoResolution,
            (v) => onChange(value.videoResolutions
              ? { ...value, videoResolutions: { ...value.videoResolutions, [executingVideo]: v } }
              : { ...value, videoResolution: v }),
            usesReferenceImages && value.videoResolutions
              ? `${bucketLabels.r2v.label} · ${t("resolution_label")}`
              : t("resolution_label"),
          )}
          {capabilities.videoModelError && <InlineWarning message={capabilities.videoModelError} />}

          {/* 档位空集且该模型的时长本就不由 ArcReel 驱动：控件无从渲染，但要说清为什么没有，
              否则用户只会看见时长这一节凭空消失。 */}
          {showDuration && supportedDurations?.length === 0 && videoDurationNotDriven && (
            <div className="flex flex-col gap-2">
              <FieldTitle>{t("duration_label")}</FieldTitle>
              <p className="text-xs text-muted-foreground">{t("duration_not_driven_notice")}</p>
            </div>
          )}

          {showDuration && supportedDurations && supportedDurations.length > 0 && (
            <div className="flex flex-col gap-2">
              <FieldTitle>{t("duration_label")}</FieldTitle>
              {isContinuousIntegerRange(supportedDurations) && supportedDurations.length >= 5 ? (
                <DurationSlider
                  options={supportedDurations}
                  value={value.defaultDuration}
                  onChange={handleDurationClick}
                  ariaLabel={t("duration_label")}
                  autoLabel={t("duration_auto")}
                  disabled={durationStale}
                />
              ) : (
                <DurationButtonGroup
                  options={supportedDurations}
                  value={value.defaultDuration}
                  onChange={handleDurationClick}
                  ariaLabel={t("duration_label")}
                  autoLabel={t("duration_auto")}
                  disabled={durationStale}
                />
              )}
              {durationNoticeKey && (
                <InlineWarning
                  message={t(durationNoticeKey, { value: value.defaultDuration })}
                  action={{
                    label: t("duration_reset_auto"),
                    onClick: () => handleDurationClick(null),
                  }}
                />
              )}
            </div>
          )}

          {onVideoGenerateAudioChange && (
            <div className="flex flex-col gap-2">
              <FieldTitle>{t("dashboard:generate_audio_label")}</FieldTitle>
              <RadioGroup
                aria-label={t("dashboard:audio_settings_sr_label")}
                // 三态值映射成字符串：RadioGroup 的值不能是 null
                value={String(audioDisplayValue)}
                onValueChange={(next) => onVideoGenerateAudioChange(next === "null" ? null : next === "true")}
                disabled={audioLocked}
              >
                {(
                  [
                    [null, t("dashboard:follow_global_default")],
                    [true, t("dashboard:enabled_label")],
                    [false, t("dashboard:disabled_label")],
                  ] as const
                ).map(([val, label]) => (
                  <Label key={String(val)}>
                    <RadioGroupItem value={String(val)} />
                    {label}
                  </Label>
                ))}
              </RadioGroup>
              {audioLockedHint && <p className="text-xs text-muted-foreground">{audioLockedHint}</p>}
              {audioConflict && (
                <InlineWarning
                  message={t("dashboard:audio_switch_conflict_notice")}
                  action={{
                    label: t("dashboard:audio_switch_conflict_action"),
                    onClick: () => onVideoGenerateAudioChange(true),
                  }}
                />
              )}
            </div>
          )}
          </LayeredModelFields>
        </ChannelCard>
      )}

      {showImage && (
        <ChannelCard title={t("model_image")}>
          <LayeredModelFields
            defaultLabel={t("model_image_default")}
            defaultValue={value.imageBackendDefault}
            defaultOptions={options.imageBackends}
            onDefaultChange={handleImageChange}
            emptyLabel={t("use_global_default")}
            emptyHint={
              globalDefaults.image
                ? t("current_global_default", { value: globalDefaults.image })
                : undefined
            }
            defaultEffective={globalDefaults.image || undefined}
            providerNames={options.providerNames}
            modelNames={options.modelNames}
            subFields={imageSubFields}
            subFieldsError={mediaCandidatesError}
          >
            {renderResolutionField(executingImage, value.imageResolution, (v) =>
              onChange({ ...value, imageResolution: v }),
            )}
          </LayeredModelFields>
        </ChannelCard>
      )}

      {showText && (
        <ChannelCard title={t("model_text")}>
          <TextTierFields
            value={{
              default: value.textBackendDefault,
              simple: value.textBackendSimple,
              complex: value.textBackendComplex,
            }}
            onChange={(next) =>
              onChange({
                ...value,
                textBackendDefault: next.default,
                textBackendSimple: next.simple,
                textBackendComplex: next.complex,
              })
            }
            options={options.textBackends}
            providerNames={options.providerNames}
            modelNames={options.modelNames}
            defaultLabel={t("use_global_default")}
            fallbacks={{
              // 项目优先解析链（docs/adr/0051）：档位留空时的实际生效值。
              // default 档回退全局默认模型；simple/complex 先看本表单的项目默认模型，
              // 再全局对应档，最后全局默认模型。
              default: effectiveModel(globalDefaults.textDefault),
              simple: effectiveModel(
                value.textBackendDefault,
                globalDefaults.textSimple,
                globalDefaults.textDefault,
              ),
              complex: effectiveModel(
                value.textBackendDefault,
                globalDefaults.textComplex,
                globalDefaults.textDefault,
              ),
            }}
            showTiers={showSubFields}
          />
        </ChannelCard>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Duration sub-components
// ---------------------------------------------------------------------------

/**
 * 时长档位的单选项：选中项用 secondary 底色。过期上下文下用 aria-disabled 而不是 disabled，
 * 选项仍可聚焦，不打断键盘走位。
 */
function DurationOption({
  checked,
  label,
  tabIndex,
  disabled,
  onClick,
}: {
  checked: boolean;
  label: string;
  tabIndex?: number;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <Button
      variant={checked ? "secondary" : "outline"}
      size="sm"
      role="radio"
      aria-checked={checked}
      aria-label={label}
      aria-disabled={disabled || undefined}
      tabIndex={tabIndex}
      onClick={onClick}
    >
      {label}
    </Button>
  );
}

function DurationButtonGroup({
  options,
  value,
  onChange,
  ariaLabel,
  autoLabel,
  disabled = false,
}: {
  options: readonly number[];
  value: number | null;
  onChange: (next: number | null) => void;
  ariaLabel: string;
  autoLabel: string;
  /** 选项属于已过期的约束上下文：仍可聚焦（不打断键盘走位），但不接受选择。 */
  disabled?: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const isAutoActive = value === null;
  // 存值越界（既非 null 又不在 options 内）时无任何 radio 选中——roving tabindex 下需让 auto
  // 兜底为可聚焦入口，否则整个 radiogroup 无 tabIndex=0 元素，键盘 Tab 无法触达、用户无从重选。
  const hasActiveOption = value !== null && options.includes(value);
  const isAutoTabbable = isAutoActive || !hasActiveOption;
  const select = (next: number | null) => {
    if (!disabled) onChange(next);
  };
  return (
    <div
      // 过期选项保留聚焦而不用原生 disabled，按钮自带的禁用淡化不生效，由整组淡化表明不可选
      className={cn("flex flex-wrap gap-2", disabled && "opacity-50")}
      role="radiogroup"
      aria-label={ariaLabel}
      aria-disabled={disabled || undefined}
    >
      <DurationOption
        checked={isAutoActive}
        label={autoLabel}
        tabIndex={isAutoTabbable ? 0 : -1}
        disabled={disabled}
        onClick={() => select(null)}
      />
      {options.map((d) => {
        const active = value === d;
        return (
          <DurationOption
            key={d}
            checked={active}
            label={t("duration_seconds_value_text", { value: d })}
            tabIndex={active ? 0 : -1}
            disabled={disabled}
            onClick={() => select(d)}
          />
        );
      })}
    </div>
  );
}

function DurationSlider({
  options,
  value,
  onChange,
  ariaLabel,
  autoLabel,
  disabled = false,
}: {
  options: readonly number[];
  value: number | null;
  onChange: (next: number | null) => void;
  ariaLabel: string;
  autoLabel: string;
  /** 同 {@link DurationButtonGroup}：过期上下文下只展示、不接受选择。 */
  disabled?: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const min = options[0];
  const max = options[options.length - 1];
  // 越界存值（非 null 且不在 options 内）的 thumb 无法落在区间内，归位到 min;但读数与
  // aria-valuetext 仍忠实显示原值——显示 auto 会与未激活的 auto 钮、以及点名秒数的越界提示
  // 自相矛盾，更让 slider 的 aria-valuetext 误报为 auto 态（实则无任何控件处于 auto）。读数
  // 只在 value 为 null（真正的 auto）时才显示 auto;越界值的不可呈现由外层越界提示兜底解释。
  const isValueInRange = value !== null && options.includes(value);
  const sliderValue = isValueInRange ? value : min;
  const isAutoActive = value === null;
  const valueText = value === null ? autoLabel : t("duration_seconds_value_text", { value });
  return (
    <div className={cn("flex flex-wrap items-center gap-3", disabled && "opacity-50")}>
      <DurationOption
        checked={isAutoActive}
        label={autoLabel}
        disabled={disabled}
        onClick={() => {
          if (!disabled) onChange(null);
        }}
      />
      <input
        type="range"
        aria-label={ariaLabel}
        aria-valuetext={valueText}
        min={min}
        max={max}
        step={1}
        value={sliderValue}
        disabled={disabled}
        onChange={(e) => onChange(parseInt(e.target.value, 10))}
        className="min-w-30 flex-1 accent-primary"
      />
      <span className="min-w-10 text-right text-xs tabular-nums text-subtle-foreground">
        {valueText}
      </span>
    </div>
  );
}
