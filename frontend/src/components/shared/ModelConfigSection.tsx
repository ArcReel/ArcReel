import { useEffect, useMemo, type ReactNode } from "react";
import { cn } from "cn";
import { useTranslation } from "react-i18next";
import { RotateCcw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { InlineWarning } from "@/components/shared/InlineWarning";
import { TruncatedText } from "@/components/shared/TruncatedText";
import {
  durationOutOfRangeReason,
  useModelCapabilities,
} from "@/hooks/useModelCapabilities";
import {
  catalogDurations,
  lookupCatalogVideoAudio,
  lookupEndpointConstraints,
  lookupQualities,
  lookupResolutions,
  lookupVideoAudioControl,
  resolutionPlaceholder,
} from "@/utils/provider-models";
import { isContinuousIntegerRange } from "@/utils/duration_format";
import { ResolutionPicker } from "./ResolutionPicker";
import { LayeredModelFields, useGenerationTypeBucketLabels, type LayeredSubField } from "./LayeredModelFields";
import { degradeSubFieldsToSaved, effectiveModel, executingImageModel, executingVideoModel } from "./model-layers";
import { TextTierFields } from "./TextTierFields";
import { CHANNEL_MODEL_FIELDS, channelOverridden, countModelOverrides, type ModelChannel } from "./model-overrides";
import { videoOptionMetaRenderer } from "./video-option-meta";
import { VideoModelSpecBar } from "./VideoModelSpecBar";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type { ProviderInfo, VoiceConsistencyTier } from "@/types/provider";
import type { CustomProviderInfo } from "@/types/custom-provider";
import type { AutoBackendsResponse, ModelCandidatesResponse } from "@/types/system";

const EMPTY_CUSTOM_PROVIDERS: CustomProviderInfo[] = [];

export interface ModelConfigValue {
  videoBackend: string;
  videoProviderI2V: string;
  videoProviderR2V: string;
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
  imageResolutions?: Record<string, string | null>;
  imageQuality: string | null;
}

export interface ModelConfigSectionProps {
  value: ModelConfigValue;
  onChange: (next: ModelConfigValue) => void;
  projectName?: string | null;
  options: {
    videoBackends: string[];
    imageBackends: string[];
    textBackends: string[];
    providerNames: Record<string, string>;
    modelNames?: Record<string, string>;
  };
  showSubFields?: boolean;
  candidates?: ModelCandidatesResponse | null;
  candidatesError?: { onRetry: () => void; retrying?: boolean };
  providers: ProviderInfo[];
  customProviders?: CustomProviderInfo[];
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
    auto?: AutoBackendsResponse | null;
  };
  videoGenerateAudio?: boolean | null;
  globalVideoGenerateAudio?: boolean;
  onVideoGenerateAudioChange?: (next: boolean | null) => void;
  usesReferenceImages?: boolean;
  showOverrideSources?: boolean;
  enable?: {
    video?: boolean;
    image?: boolean;
    text?: boolean;
    duration?: boolean;
  };
}

function ChannelCard({ title, source, children }: { title: string; source?: ReactNode; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <div className="flex min-w-0 items-center justify-between gap-3">
        <h3 className="shrink-0 text-sm font-medium">{title}</h3>
        {source}
      </div>
      {children}
    </section>
  );
}

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
  showOverrideSources = false,
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

  const executingVideo = executingVideoModel(value, globalDefaults, usesReferenceImages);
  const executingI2V = executingVideoModel(value, globalDefaults, false);
  const executingImage = executingImageModel(value, globalDefaults, "t2i");
  const executingImageI2I = executingImageModel(value, globalDefaults, "i2i");

  const mediaCandidates = showSubFields ? candidates : null;
  const mediaCandidatesError = showSubFields ? candidatesError : undefined;

  const withVideoLayer = (base: ModelConfigValue, patch: Partial<ModelConfigValue>): ModelConfigValue => {
    const next = { ...base, ...patch };
    const nextExecuting = executingVideoModel(next, globalDefaults, usesReferenceImages);
    if (nextExecuting === executingVideoModel(base, globalDefaults, usesReferenceImages)) return next;
    const nextDurations = catalogDurations(providers, customProviders, nextExecuting);
    const keepDuration = next.defaultDuration !== null && !!nextDurations?.includes(next.defaultDuration);
    return {
      ...next,
      defaultDuration: keepDuration ? next.defaultDuration : null,
      videoResolution: base.videoResolutions ? (base.videoResolutions[nextExecuting] ?? null) : null,
    };
  };

  const withImageLayer = (base: ModelConfigValue, patch: Partial<ModelConfigValue>): ModelConfigValue => {
    const next = { ...base, ...patch };
    const nextExecuting = executingImageModel(next, globalDefaults, "t2i");
    return nextExecuting === executingImageModel(base, globalDefaults, "t2i")
      ? next
      : {
          ...next,
          imageResolution: base.imageResolutions ? (base.imageResolutions[nextExecuting] ?? null) : null,
          imageQuality: null,
        };
  };

  const applyVideoLayer = (patch: Partial<ModelConfigValue>) => onChange(withVideoLayer(value, patch));
  const applyImageLayer = (patch: Partial<ModelConfigValue>) => onChange(withImageLayer(value, patch));

  const clearedFields = (channel: ModelChannel): Partial<ModelConfigValue> =>
    Object.fromEntries(CHANNEL_MODEL_FIELDS[channel].map((field) => [field, ""]));
  const resetChannel = (channel: ModelChannel) => {
    if (channel === "video") {
      applyVideoLayer(clearedFields("video"));
      onVideoGenerateAudioChange?.(null);
    } else if (channel === "image") {
      applyImageLayer(clearedFields("image"));
    } else {
      onChange({ ...value, ...clearedFields("text") });
    }
  };
  const resetAllChannels = () => {
    onChange({
      ...withImageLayer(withVideoLayer(value, clearedFields("video")), clearedFields("image")),
      ...clearedFields("text"),
    });
    onVideoGenerateAudioChange?.(null);
  };

  const videoSubFields: LayeredSubField[] | undefined = showSubFields
    ? degradeSubFieldsToSaved([
        {
          key: "i2v",
          ...bucketLabels.i2v,
          bucket: "i2v",
          value: value.videoProviderI2V,
          options: mediaCandidates?.video.buckets.i2v ?? [],
          filtered: mediaCandidates?.video.filtered.i2v,
          effective: effectiveModel(value.videoBackend, globalDefaults.videoI2V, globalDefaults.video),
          auto: globalDefaults.auto?.i2v,
          onChange: (next: string) => applyVideoLayer({ videoProviderI2V: next }),
        },
        {
          key: "r2v",
          ...bucketLabels.r2v,
          bucket: "r2v",
          value: value.videoProviderR2V,
          options: mediaCandidates?.video.buckets.r2v ?? [],
          filtered: mediaCandidates?.video.filtered.r2v,
          effective: effectiveModel(value.videoBackend, globalDefaults.videoR2V, globalDefaults.video),
          auto: globalDefaults.auto?.r2v,
          onChange: (next: string) => applyVideoLayer({ videoProviderR2V: next }),
        },
      ], !!mediaCandidates)
    : undefined;

  const imageSubFields: LayeredSubField[] | undefined = showSubFields
    ? degradeSubFieldsToSaved([
        {
          key: "t2i",
          ...bucketLabels.t2i,
          bucket: "t2i",
          value: value.imageBackendT2I,
          options: mediaCandidates?.image.buckets.t2i ?? [],
          filtered: mediaCandidates?.image.filtered.t2i,
          effective: effectiveModel(value.imageBackendDefault, globalDefaults.imageT2I, globalDefaults.image),
          auto: globalDefaults.auto?.t2i,
          onChange: (next: string) => applyImageLayer({ imageBackendT2I: next }),
        },
        {
          key: "i2i",
          ...bucketLabels.i2i,
          bucket: "i2i",
          value: value.imageBackendI2I,
          options: mediaCandidates?.image.buckets.i2i ?? [],
          filtered: mediaCandidates?.image.filtered.i2i,
          effective: effectiveModel(value.imageBackendDefault, globalDefaults.imageI2I, globalDefaults.image),
          auto: globalDefaults.auto?.i2i,
          onChange: (next: string) => applyImageLayer({ imageBackendI2I: next }),
        },
      ], !!mediaCandidates)
    : undefined;

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
  const videoModelError = (bucketReference: string, error: string | null) => {
    const reference = bucketReference ? (showSubFields ? bucketReference : "") : value.videoBackend;
    const status = reference ? candidates?.lifecycle?.[reference]?.status : undefined;
    return status === "discontinued" || status === "unavailable" ? null : error;
  };
  const executingVideoError = videoModelError(
    usesReferenceImages ? value.videoProviderR2V : value.videoProviderI2V,
    capabilities.videoModelError,
  );
  const executingI2VError = videoModelError(value.videoProviderI2V, i2vCapabilities.videoModelError);
  const { rawDurations, supportedDurations, voiceConsistency } = capabilities;
  const durationStale = capabilities.loading;

  const videoSpecTier: VoiceConsistencyTier | null = projectName
    ? voiceConsistency
    : (lookupCatalogVideoAudio(providers, executingVideo, customProviders)?.voiceConsistency ?? null);

  const audioControl = lookupVideoAudioControl(providers, executingVideo, usesReferenceImages ? "r2v" : "i2v", customProviders);
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
  const audioConflict =
    audioControl === "always_on" && (videoGenerateAudio ?? globalVideoGenerateAudio) === false;

  const videoDurationNotDriven =
    lookupEndpointConstraints(executingVideo, customProviders, endpointConstraints)?.durationTierEmpty ?? false;

  const videoResolutionOptions = lookupResolutions(
    providers,
    executingVideo,
    customProviders,
    endpointToMediaType,
  ).options;

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

  const durationNoticeKey = useMemo(() => {
    if (durationStale) return null;
    const reason = durationOutOfRangeReason(value.defaultDuration, capabilities);
    if (reason === null) return null;
    return {
      model: "duration_unsupported_notice",
      reference: "duration_unsupported_reference_notice",
      end_frame: "duration_unsupported_end_frame_notice",
      resolution: "duration_unsupported_resolution_notice",
    }[reason];
  }, [value.defaultDuration, capabilities, durationStale]);

  const renderResolutionField = (
    backend: string,
    resolution: string | null,
    onResolutionChange: (v: string | null) => void,
    label = t("resolution_label"),
    flagStale = false,
  ) => {
    const res = lookupResolutions(providers, backend, customProviders, endpointToMediaType);
    const hasOptions = res.options.length > 0;
    const stale = flagStale && res.isKnown && !res.isCustom && resolutionStale(resolution, res.options);
    if (!hasOptions && !stale) return null;
    const constraints = lookupEndpointConstraints(backend, customProviders, endpointConstraints);
    const sizeFixed = constraints?.sizeFixed ?? false;
    return (
      <div className="flex flex-col gap-1">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-muted-foreground">{label}</span>
          {hasOptions && (
            <ResolutionPicker
              mode={res.isCustom ? "combobox" : "select"}
              options={res.options}
              value={resolution}
              onChange={onResolutionChange}
              placeholder={resolutionPlaceholder(constraints, t)}
              aria-label={label}
              disabled={sizeFixed}
            />
          )}
        </div>
        {sizeFixed && <p className="text-xs text-muted-foreground">{t("resolution_fixed_hint")}</p>}
        {stale && (
          <InlineWarning
            message={t("resolution_unavailable_notice", { value: resolution })}
            action={{ label: t("resolution_reset_default"), onClick: () => onResolutionChange(null) }}
          />
        )}
      </div>
    );
  };

  const renderImageQualityField = () => {
    const qualities = lookupQualities(providers, executingImage, customProviders);
    if (qualities.length === 0) return null;
    const quality = storedValue(value.imageQuality);
    const stale = quality !== null && !qualities.includes(quality);
    const setQuality = (next: string | null) => onChange({ ...value, imageQuality: next });
    return (
      <div className="flex flex-col gap-1">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-muted-foreground">{t("quality_label")}</span>
          <ResolutionPicker
            mode="select"
            options={qualities}
            value={quality}
            onChange={setQuality}
            placeholder={t("quality_auto_placeholder")}
            aria-label={t("quality_label")}
          />
        </div>
        {stale && (
          <InlineWarning
            message={t("quality_unavailable_notice", { value: quality })}
            action={{ label: t("quality_reset_auto"), onClick: () => setQuality(null) }}
          />
        )}
      </div>
    );
  };

  const audioOverride = onVideoGenerateAudioChange ? (videoGenerateAudio ?? null) : null;
  const overrideCount = countModelOverrides(value, audioOverride);

  const namedModel = (fullValue: string) => {
    const idx = fullValue.indexOf("/");
    if (idx === -1) return options.providerNames[fullValue] || fullValue;
    return options.modelNames?.[fullValue] || fullValue.slice(idx + 1);
  };
  const globalChannelModel: Record<ModelChannel, string | undefined> = {
    video: effectiveModel(usesReferenceImages ? globalDefaults.videoR2V : globalDefaults.videoI2V, globalDefaults.video),
    image: effectiveModel(globalDefaults.imageT2I, globalDefaults.image),
    text: effectiveModel(globalDefaults.textDefault),
  };
  const channelAuto: Record<ModelChannel, string | null | undefined> = {
    video: globalChannelModel.video ? undefined : globalDefaults.auto?.[usesReferenceImages ? "r2v" : "i2v"],
    image: globalChannelModel.image ? undefined : globalDefaults.auto?.t2i,
    text: globalChannelModel.text ? undefined : globalDefaults.auto?.text,
  };
  const channelModelLabel = (channel: ModelChannel) => {
    const model = globalChannelModel[channel];
    if (model) return namedModel(model);
    const auto = channelAuto[channel];
    if (auto === null) return t("dashboard:auto_select_unavailable");
    return auto ? t("dashboard:auto_select_current", { model: namedModel(auto) }) : t("dashboard:auto_select");
  };

  const channelSource = (channel: ModelChannel, title: string) => {
    if (!showOverrideSources) return undefined;
    if (!channelOverridden(channel, value, audioOverride)) {
      return (
        <TruncatedText
          text={t("dashboard:model_follow_global_source", { model: channelModelLabel(channel) })}
          className="text-xs text-muted-foreground"
        />
      );
    }
    return (
      <div className="flex shrink-0 items-center gap-1.5">
        <Badge variant="secondary">{t("dashboard:model_project_source")}</Badge>
        <Button
          variant="ghost"
          size="xs"
          aria-label={t("dashboard:model_channel_reset_aria", { channel: title })}
          onClick={() => resetChannel(channel)}
        >
          {t("dashboard:model_channel_reset")}
        </Button>
      </div>
    );
  };

  const splitImageResolution = Boolean(
    value.imageResolutions &&
      executingImage &&
      executingImageI2I &&
      executingImage !== executingImageI2I,
  );
  const t2iResolution = value.imageResolutions
    ? (value.imageResolutions[executingImage] ?? null)
    : value.imageResolution;
  const i2iResolution = value.imageResolutions
    ? (value.imageResolutions[executingImageI2I] ?? null)
    : null;
  const setImageResolution = (model: string, next: string | null) =>
    onChange(
      value.imageResolutions
        ? {
            ...value,
            imageResolution: model === executingImage ? next : value.imageResolution,
            imageResolutions: { ...value.imageResolutions, [model]: next },
          }
        : { ...value, imageResolution: next },
    );

  return (
    <div className="flex flex-col gap-4">
      {showOverrideSources ? (
        <div className="flex min-h-10 items-center justify-between gap-3 rounded-lg border border-border bg-muted/40 px-3 py-1.5 text-sm">
          <p className="text-subtle-foreground">
            {overrideCount === 0
              ? t("dashboard:model_overrides_none")
              : t("dashboard:model_overrides_notice", { count: overrideCount })}
          </p>
          {overrideCount > 0 && (
            <Button variant="ghost" size="sm" onClick={resetAllChannels}>
              <RotateCcw data-icon="inline-start" />
              {t("dashboard:model_overrides_reset_all")}
            </Button>
          )}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">{t("default_hint")}</p>
      )}

      {showVideo && (
        <ChannelCard title={t("model_video")} source={channelSource("video", t("model_video"))}>
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
            defaultAuto={channelAuto.video}
            providerNames={options.providerNames}
            modelNames={options.modelNames}
            renderOptionMeta={renderVideoOptionMeta}
            subFields={videoSubFields}
            subFieldsError={mediaCandidatesError}
            lifecycle={candidates?.lifecycle}
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
          {usesReferenceImages && executingI2VError && <InlineWarning message={executingI2VError} />}
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
          {executingVideoError && <InlineWarning message={executingVideoError} />}

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
        <ChannelCard title={t("model_image")} source={channelSource("image", t("model_image"))}>
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
            defaultAuto={channelAuto.image}
            providerNames={options.providerNames}
            modelNames={options.modelNames}
            subFields={imageSubFields}
            subFieldsError={mediaCandidatesError}
            lifecycle={candidates?.lifecycle}
          >
            {splitImageResolution ? (
              <>
                {renderResolutionField(
                  executingImage,
                  t2iResolution,
                  (v) => setImageResolution(executingImage, v),
                  `${bucketLabels.t2i.label} · ${t("resolution_label")}`,
                  true,
                )}
                {renderResolutionField(
                  executingImageI2I,
                  i2iResolution,
                  (v) => setImageResolution(executingImageI2I, v),
                  `${bucketLabels.i2i.label} · ${t("resolution_label")}`,
                  true,
                )}
              </>
            ) : executingImage ? (
              renderResolutionField(
                executingImage,
                t2iResolution,
                (v) => setImageResolution(executingImage, v),
                t("resolution_label"),
                true,
              )
            ) : (
              value.imageResolutions &&
              executingImageI2I &&
              renderResolutionField(
                executingImageI2I,
                i2iResolution,
                (v) => setImageResolution(executingImageI2I, v),
                `${bucketLabels.i2i.label} · ${t("resolution_label")}`,
                true,
              )
            )}
            {renderImageQualityField()}
          </LayeredModelFields>
        </ChannelCard>
      )}

      {showText && (
        <ChannelCard title={t("model_text")} source={channelSource("text", t("model_text"))}>
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
            auto={globalDefaults.auto?.text}
            showTiers={showSubFields}
            lifecycle={candidates?.lifecycle}
          />
        </ChannelCard>
      )}
    </div>
  );
}


function storedValue(stored: string | null): string | null {
  const trimmed = stored?.trim();
  return trimmed ? trimmed : null;
}

function resolutionStale(stored: string | null, options: readonly string[]): boolean {
  const value = storedValue(stored)?.toLowerCase();
  return value !== undefined && !options.some((option) => option.trim().toLowerCase() === value);
}



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
  disabled?: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const isAutoActive = value === null;
  // 兜底为可聚焦入口，否则整个 radiogroup 无 tabIndex=0 元素，键盘 Tab 无法触达、用户无从重选。
  const hasActiveOption = value !== null && options.includes(value);
  const isAutoTabbable = isAutoActive || !hasActiveOption;
  const select = (next: number | null) => {
    if (!disabled) onChange(next);
  };
  return (
    <div
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
  disabled?: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const min = options[0];
  const max = options[options.length - 1];
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
