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
  /** Project default video models; both empty video subdivisions fall back here, then globally. */
  videoBackend: string;
  videoProviderI2V: string;
  videoProviderR2V: string;
  /** Project default image models; both empty image subdivisions fall back here, then globally. */
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
   * Owning project names, supplied for existing projects (settings); creation-wizard projects do not yet exist,
   * so omission makes capability pipelines query projectless candidate-model endpoints.
   */
  projectName?: string | null;
  options: {
    videoBackends: string[];
    imageBackends: string[];
    textBackends: string[];
    providerNames: Record<string, string>;
    /** "provider/model" → current-language names sharing `providerNames` sources; missing keys fall back to model IDs. */
    modelNames?: Record<string, string>;
  };
  /**
   * Whether to show Specify models by use layers (media subdivisions/text tiers). False leaves defaults only,
   * used by creation wizards. Unlike candidates, this expresses intent; candidates provide media-subdivision data only,
   * so failed fetching does not affect text tiers.
   */
  showSubFields?: boolean;
  /**
   * Media subdivision candidates (docs/adr/0054). Absent after fetch failures: retain configured subdivisions only,
   * with current values as sole candidates; omit unconfigured rows, hiding entire collapsible regions if all are unconfigured.
   */
  candidates?: ModelCandidatesResponse | null;
  /**
   * Candidate fetch failures/retry entries; nonempty values show errors in both video/image collapsible regions. Distinct from
   * missing `candidates` while still loading, which uses silent fallback above without errors.
   * Text tiers use no candidate data and do not participate.
   */
  candidatesError?: { onRetry: () => void; retrying?: boolean };
  providers: ProviderInfo[];
  customProviders?: CustomProviderInfo[];
  /** Global configured layer values passed unchanged without caller-side fallback folding; derive full resolution locally along chains. */
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
    /** Backend automatic models when all layers are empty (`API.getAutoBackends`); absent values neither display nor enter capability queries. */
    auto?: AutoBackendsResponse | null;
  };
  /**
   * Project Generate audio video overrides (null follows global, true/false explicit overrides).
   * Render within video channels only when onVideoGenerateAudioChange exists; this controls video-model capabilities,
   * unrelated to narration TTS, so belongs within video channels rather than standalone. Creation wizards omit it to hide controls.
   */
  videoGenerateAudio?: boolean | null;
  /**
   * Effective global Generate audio video values resolve `videoGenerateAudio` null (follow global) into actual
   * values for contradiction hints; otherwise empty projects following global Off would be undetectable. Omission means On.
   */
  globalVideoGenerateAudio?: boolean;
  onVideoGenerateAudioChange?: (next: boolean | null) => void;
  /**
   * Whether projects use reference-to-video (direct asset-image output). Some models restrict reference-path durations to single values;
   * filter duration options accordingly. Owning pages know modes, supplying them externally instead of inference here.
   */
  usesReferenceImages?: boolean;
  /**
   * Show channel origins (project settings): top explanations count overridden global fields and offer Restore all; channel headings
   * show This project/Restore global or Follow global ·Global default model name.
   */
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

/** Channel field subheadings: localized regular text, never monospace uppercase. */
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

  // Duration/resolution/voice tiers query actual execution-model capabilities; overridden subdivisions mean
  // default-layer models differ, so querying defaults would suggest unsupported runtime durations/resolutions.
  const executingVideo = executingVideoModel(value, globalDefaults, usesReferenceImages);
  const executingI2V = executingVideoModel(value, globalDefaults, false);
  const executingImage = executingImageModel(value, globalDefaults);

  // Full project-priority resolution (docs/adr/0054): empty subdivision → project default → global same-name subdivision → global default.
  const mediaCandidates = showSubFields ? candidates : null;
  const mediaCandidatesError = showSubFields ? candidatesError : undefined;

  // Durations/resolutions follow execution models; every default/subdivision edit first checks whether execution models changed:
  // unchanged (e.g. default edits under overridden subdivisions) writes unchanged; changed models clear inapplicable resolutions and reset durations
  // outside newly declared complete sets to Automatic. Both paths share handling, avoiding validation only on primary selects.
  // Complete sets exist synchronously in catalogs; reference paths may further narrow new-model values, with cause-specific hints below guiding reselection,
  // because event handlers cannot read server narrowing results.
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
    const nextExecuting = executingImageModel(next, globalDefaults);
    return nextExecuting === executingImageModel(base, globalDefaults) ? next : { ...next, imageResolution: null };
  };

  const applyVideoLayer = (patch: Partial<ModelConfigValue>) => onChange(withVideoLayer(value, patch));
  const applyImageLayer = (patch: Partial<ModelConfigValue>) => onChange(withImageLayer(value, patch));

  // Restore global clears channel model fields, correcting durations/resolutions through select paths; video channels also restore Generate audio video.
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

  // All capabilities come through useModelCapabilities (see its source-of-truth rules); this component never queries tables itself.
  // Forms use unsaved candidate-model/resolution edits, explicitly passing them for server evaluation; null resolution
  // means Automatic without falling back to saved tiers. Projectless creation wizards use projectless endpoints.
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
  // When this execution-path model comes from this card references (subdivision before default) and already shows Retired/Unavailable
  // notices there, capability errors describe the same issue and are suppressed. Retiring models remain callable, so errors have other causes and remain visible; execution models
  // from global defaults or subdivisions hidden here lack corresponding notices, so also show errors. Other paths referencing identical models do not count.
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
  // Changed constraint contexts (resolution/reference paths) with unchanged models retain old narrowed results until new results arrive:
  // switching tiers avoids loading flashes, but interim options belong to previous contexts. Display only, without selection,
  // preventing users choosing new-context-invalid durations from stale lists.
  const durationStale = capabilities.loading;

  // Voice-consistency tiers: project contexts derive server-side from candidate models × project generation_mode (capability queries
  // already include videoBackend, covering unsaved selections); without projects, read same-named catalog-endpoint fields,
  // likewise server-derived. Neither frontend path contains derivation formulas.
  const videoSpecTier: VoiceConsistencyTier | null = projectName
    ? voiceConsistency
    : (lookupCatalogVideoAudio(providers, executingVideo)?.voiceConsistency ?? null);

  // Audio toggles follow execution-model controllability; always-audio/never-audio models receive no toggle, so disable controls and show actual output
  // audio rather than stored configuration. Existing Off values get one-click corrections below, never silent rewrites.
  // Resolve by path, not model: Kling v3-omni controls image-to-video audio but has no reference-to-video toggle; model-only lookup would permit
  // controls reference-to-video execution necessarily discards.
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
  // Detect contradictions from effective values: project null follows global, so global Off also conflicts with always-audio models.
  const audioConflict =
    audioControl === "always_on" && (videoGenerateAudio ?? globalVideoGenerateAudio) === false;

  // Endpoints providing no duration tiers truthfully declare empty sets rather than configuration defects (docs/adr/0082). Use
  // durationTierEmpty rather than durationFixed: bound frames without readable frame-rate sources also yield empty tiers,
  // giving project-page users the same This dimension is not controlled by ArcReel result.
  const videoDurationNotDriven =
    lookupEndpointConstraints(executingVideo, customProviders, endpointConstraints)?.durationTierEmpty ?? false;

  const videoResolutionOptions = lookupResolutions(
    providers,
    executingVideo,
    customProviders,
    endpointToMediaType,
  ).options;

  // Default selects show actual project execution paths (same criteria as audioControl above); subdivision selects
  // use their own buckets independently.
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

  // Saved durations outside current supported sets (unchanged backends, externally narrowed sets during mounting/rerendering):
  // never silently mutate; show hints/one-click auto fallback, preserving awareness/reselection opportunities.
  //
  // Separate hint wording by cause: Model unsupported only when complete model sets exclude values; contextual narrowing explains
  // resolution versus reference-path causes, guiding relevant settings edits rather than model replacements.
  const durationNoticeKey = useMemo(() => {
    // Stale contexts cannot establish causes; offer neither hints nor No problems, both misleading;
    // controls reject selection during the same window; new results recompute hints against new contexts.
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
    // Workflow-fixed dimensions make selectors display-only: ratios/resolutions do not affect these model rows; accepting then discarding
    // would falsely imply selected tiers applied.
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
        {/* Disabled reasons require visible explanation lines; titles are inaccessible to keyboards/touch. */}
        {sizeFixed && <p className="text-xs text-muted-foreground">{t("resolution_fixed_hint")}</p>}
      </div>
    );
  };

  // Projects record only overridden fields; count Generate audio video only when rendered in this form.
  const audioOverride = onVideoGenerateAudioChange ? (videoGenerateAudio ?? null) : null;
  const overrideCount = countModelOverrides(value, audioOverride);

  const namedModel = (fullValue: string) => {
    const idx = fullValue.indexOf("/");
    if (idx === -1) return options.providerNames[fullValue] || fullValue;
    return options.modelNames?.[fullValue] || fullValue.slice(idx + 1);
  };
  // Effective inherited global models: video uses project-path subdivisions, images text-to-image, text default tiers.
  const globalChannelModel: Record<ModelChannel, string | undefined> = {
    video: effectiveModel(usesReferenceImages ? globalDefaults.videoR2V : globalDefaults.videoI2V, globalDefaults.video),
    image: effectiveModel(globalDefaults.imageT2I, globalDefaults.image),
    text: effectiveModel(globalDefaults.textDefault),
  };
  // Automatic selections for globally empty channels follow globalChannelModel paths. Present only when all global layers are empty:
  // default-select fallbackValue sees only global defaults; nonempty global subdivisions must not appear Automatic.
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

          {/* Empty tiers where model duration is inherently outside ArcReel control cannot render controls, but require explanations,
              otherwise duration sections would seemingly vanish. */}
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
                // Convert tristates into strings: RadioGroup values cannot be null.
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
            {renderResolutionField(executingImage, value.imageResolution, (v) =>
              onChange({ ...value, imageResolution: v }),
            )}
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
              // Project-priority chains (docs/adr/0051): effective values for empty tiers.
              // default falls back to global default models; simple/complex first consult this form project defaults,
              // then matching global tiers, finally global defaults.
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

// ---------------------------------------------------------------------------
// Duration sub-components
// ---------------------------------------------------------------------------

/**
 * Duration radio options: selected values use secondary fills. Stale contexts use aria-disabled rather than disabled,
 * preserving focus and keyboard movement.
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
  /** Options belong to stale constraint contexts; retain focus/keyboard movement but reject selection. */
  disabled?: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const isAutoActive = value === null;
  // Out-of-range saved values (nonnull and absent from options) select no radios; roving tabindex needs auto
  // as focusable fallbacks, otherwise entire radiogroups lack tabIndex=0 and keyboard Tab cannot reach reselection.
  const hasActiveOption = value !== null && options.includes(value);
  const isAutoTabbable = isAutoActive || !hasActiveOption;
  const select = (next: number | null) => {
    if (!disabled) onChange(next);
  };
  return (
    <div
      // Stale options remain focusable without native disabled; button disabled fading does not apply, so dim entire groups to signal unavailability.
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
  /** Like {@link DurationButtonGroup}: stale contexts display only, rejecting selection. */
  disabled?: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const min = options[0];
  const max = options[options.length - 1];
  // Out-of-range saved-value thumbs (nonnull, outside options) cannot fit ranges, so reset to min; readouts/
  // aria-valuetext faithfully retain original values. Showing auto would contradict inactive auto buttons and second-specific out-of-range hints,
  // falsely reporting auto through slider aria-valuetext when no controls are auto. Readouts
  // show auto only for null values (true auto); outer out-of-range hints explain unrepresentable values.
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
