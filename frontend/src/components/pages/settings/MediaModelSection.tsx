
import { useState, useEffect, useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "wouter";
import { Loader2 } from "lucide-react";
import { API } from "@/api";
import { settingsSectionPath } from "@/app-routes";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type {
  AutoBackendsResponse,
  SystemConfigSettings,
  SystemConfigOptions,
  SystemConfigPatch,
} from "@/types/system";
import type { CustomProviderInfo } from "@/types/custom-provider";
import { ProviderModelSelect } from "@/components/shared/ProviderModelSelect";
import {
  LayeredModelFields,
  useGenerationTypeBucketLabels,
  type LayeredSubField,
} from "@/components/shared/LayeredModelFields";
import { degradeSubFieldsToSaved } from "@/components/shared/model-layers";
import { TextTierFields } from "@/components/shared/TextTierFields";
import { videoOptionMetaRenderer } from "@/components/shared/video-option-meta";
import { VideoModelSpecBar } from "@/components/shared/VideoModelSpecBar";
import { InlineWarning } from "@/components/shared/InlineWarning";
import { ModelLifecycleNotice } from "@/components/shared/ModelLifecycleNotice";
import { NarrationVoiceInput } from "@/components/shared/NarrationVoiceInput";
import { SaveBar } from "@/components/shared/edit-unit/SaveBar";
import { PageShellFooter } from "@/components/shared/page-shell/PageShell";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { useCapabilitiesStore } from "@/stores/capabilities-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import { useDisplayNames } from "@/hooks/useDisplayNames";
import { useModelCandidates } from "@/hooks/useModelCandidates";
import { useTtsModelCapabilities } from "@/hooks/useTtsModelCapabilities";
import {
  catalogDurations,
  getCustomProviderModels,
  getProviderModels,
  lookupCatalogVideoAudio,
  lookupResolutions,
  lookupVideoAudioControl,
} from "@/utils/provider-models";
import type { ProviderInfo, VideoRoute } from "@/types/provider";

/** System-setting fields in this edit unit; submit changed fields only. */
const MEDIA_MODEL_KEYS = [
  "default_video_backend",
  "default_video_backend_i2v",
  "default_video_backend_r2v",
  "default_image_backend",
  "default_image_backend_t2i",
  "default_image_backend_i2i",
  "default_text_backend",
  "text_backend_simple",
  "text_backend_complex",
  "default_audio_backend",
  "narration_voice",
  "narration_speed",
  "video_generate_audio",
  "video_poll_timeout_seconds",
] as const satisfies readonly (keyof SystemConfigPatch & keyof SystemConfigSettings)[];

type MediaModelFields = Pick<SystemConfigPatch, (typeof MEDIA_MODEL_KEYS)[number]>;

function fieldsFrom(settings: SystemConfigSettings | null): MediaModelFields {
  const fields: Record<string, unknown> = {};
  if (settings) for (const key of MEDIA_MODEL_KEYS) fields[key] = settings[key];
  return fields;
}

function changedFields(fields: MediaModelFields, saved: MediaModelFields): SystemConfigPatch {
  const patch: Record<string, unknown> = {};
  for (const key of MEDIA_MODEL_KEYS) {
    if (JSON.stringify(fields[key]) !== JSON.stringify(saved[key])) patch[key] = fields[key];
  }
  return patch;
}

/** One channel card: titles, optional explanations, then fields. */
function ChannelCard({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-4 rounded-lg border border-border bg-card p-4">
      <div className="flex flex-col gap-1">
        <h3 className="text-sm font-medium">{title}</h3>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      {children}
    </section>
  );
}

/** Empty states without channel models, linking to Providers configuration. */
function NoProviders({ message }: { message: string }) {
  const { t } = useTranslation("dashboard");
  return (
    <p className="rounded-md border border-dashed border-border px-3 py-2.5 text-sm text-muted-foreground">
      {message}{" "}
      <Link href={settingsSectionPath("providers")} className="text-primary underline underline-offset-4">
        {t("default_models_configure_providers")}
      </Link>
    </p>
  );
}

function FieldHint({ id, children }: { id?: string; children: React.ReactNode }) {
  return (
    <p id={id} className="text-xs text-muted-foreground">
      {children}
    </p>
  );
}

export function MediaModelSection() {
  const { t } = useTranslation("dashboard");

  const [settings, setSettings] = useState<SystemConfigSettings | null>(null);
  const [options, setOptions] = useState<SystemConfigOptions | null>(null);
  const {
    candidates,
    error: candidatesError,
    retrying: candidatesRetrying,
    reload: reloadCandidates,
  } = useModelCandidates();
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [customProviders, setCustomProviders] = useState<CustomProviderInfo[]>([]);
  // Actual automatically selected models; null on unreadable previews, dropdowns show Auto only.
  const [auto, setAuto] = useState<AutoBackendsResponse | null>(null);
  // Raw polling-timeout editing strings (null = not editing). Numeric controlled values swallow
  // intermediate "60."/empty states; parse into unsaved edits on blur.
  const [pollTimeoutInput, setPollTimeoutInput] = useState<string | null>(null);

  const endpointToMediaType = useEndpointCatalogStore((s) => s.endpointToMediaType);
  const fetchEndpointCatalog = useEndpointCatalogStore((s) => s.fetch);
  useEffect(() => {
    if (customProviders.length > 0) void fetchEndpointCatalog();
  }, [customProviders.length, fetchEndpointCatalog]);

  const { providerNames: allProviderNames, modelNames: allModelNames } = useDisplayNames(
    providers,
    customProviders,
    options,
    candidates,
  );
  const bucketLabels = useGenerationTypeBucketLabels();

  // Fetch candidates separately from configuration; independent failures affect subtiers only, preserving loaded forms
  // and avoiding page-wide retry fetches. Never await them after initiation: slow/hung candidate APIs
  // would block whole-page spinners/save flows, while subtiers already explain their own loading.
  const fetchConfig = useCallback(async () => {
    const [res, catalog, custom, autoBackends] = await Promise.all([
      API.getSystemConfig(),
      getProviderModels().catch(() => [] as ProviderInfo[]),
      getCustomProviderModels().catch(() => [] as CustomProviderInfo[]),
      API.getAutoBackends().catch(() => null),
    ]);
    setSettings(res.settings);
    setOptions(res.options);
    setProviders(catalog);
    setCustomProviders(custom);
    setAuto(autoBackends);
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- asynchronously fetch/write configuration after dependency changes
    void fetchConfig();
  }, [fetchConfig]);

  // Candidates refetch independently; reload identities change with language, refreshing candidates/translations only,
  // never fetchConfig, since whole-page configuration refetch is unnecessary.
  useEffect(() => {
    void reloadCandidates();
  }, [reloadCandidates]);

  const source = useMemo(() => fieldsFrom(settings), [settings]);
  const saveFields = useCallback(
    async (fields: MediaModelFields, saved: MediaModelFields) => {
      const res = await API.updateSystemConfig(changedFields(fields, saved));
      // Global-default video backends participate in project > system settings > system defaults resolution. Without project overrides,
      // changing globals replaces effective models without project-field changes, so active capability queries see no props-triggered refetch.
      useCapabilitiesStore.getState().invalidate();
      await fetchConfig();
      void reloadCandidates();
      void useConfigStatusStore.getState().refresh();
      // Saved values use server normalization, such as trimmed narration voices, independent of configuration-refetch render timing.
      return fieldsFrom(res.settings);
    },
    [fetchConfig, reloadCandidates],
  );
  const unit = useEditUnit({ source, save: saveFields });
  const fields = unit.value;
  const setFields = unit.setValue;
  // Empty automatic audio defaults list no voices; runtime infers models, so one vendor's voices would mislead.
  const audioVoices = useTtsModelCapabilities(fields.default_audio_backend ?? "")?.voices ?? [];

  if (!settings || !options) {
    return (
      <div className="flex items-center gap-2 py-12 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" aria-hidden />
        {t("common:loading")}
      </div>
    );
  }

  const videoBackends: string[] = options.video_backends ?? [];
  const imageBackends: string[] = options.image_backends ?? [];
  const textBackends: string[] = options.text_backends ?? [];
  const audioBackends: string[] = options.audio_backends ?? [];

  const currentVideo = fields.default_video_backend ?? "";
  const currentVideoI2V = fields.default_video_backend_i2v ?? "";
  const currentVideoR2V = fields.default_video_backend_r2v ?? "";
  const currentImage = fields.default_image_backend ?? "";
  const currentImageT2I = fields.default_image_backend_t2i ?? "";
  const currentImageI2I = fields.default_image_backend_i2i ?? "";
  const currentAudio = fields.video_generate_audio ?? false;
  const currentPollTimeout =
    fields.video_poll_timeout_seconds;

  // Globals ground resolution: empty subtiers fall back to global defaults; empty defaults mean automatic inference,
  // showing backend-preview auto models, or Auto alone without previews.
  const videoSubFields: LayeredSubField[] = degradeSubFieldsToSaved(
    [
        {
          key: "i2v",
          ...bucketLabels.i2v,
          bucket: "i2v",
          value: currentVideoI2V,
          options: candidates?.video.buckets.i2v ?? [],
          filtered: candidates?.video.filtered.i2v,
          effective: currentVideo || undefined,
          auto: auto?.i2v,
          onChange: (v) => setFields((prev) => ({ ...prev, default_video_backend_i2v: v })),
        },
        {
          key: "r2v",
          ...bucketLabels.r2v,
          bucket: "r2v",
          value: currentVideoR2V,
          options: candidates?.video.buckets.r2v ?? [],
          filtered: candidates?.video.filtered.r2v,
          effective: currentVideo || undefined,
          auto: auto?.r2v,
          onChange: (v) => setFields((prev) => ({ ...prev, default_video_backend_r2v: v })),
        },
    ],
    !!candidates,
  );

  const imageSubFields: LayeredSubField[] = degradeSubFieldsToSaved(
    [
        {
          key: "t2i",
          ...bucketLabels.t2i,
          bucket: "t2i",
          value: currentImageT2I,
          options: candidates?.image.buckets.t2i ?? [],
          filtered: candidates?.image.filtered.t2i,
          effective: currentImage || undefined,
          auto: auto?.t2i,
          onChange: (v) => setFields((prev) => ({ ...prev, default_image_backend_t2i: v })),
        },
        {
          key: "i2i",
          ...bucketLabels.i2i,
          bucket: "i2i",
          value: currentImageI2I,
          options: candidates?.image.buckets.i2i ?? [],
          filtered: candidates?.image.filtered.i2i,
          effective: currentImage || undefined,
          auto: auto?.i2i,
          onChange: (v) => setFields((prev) => ({ ...prev, default_image_backend_i2i: v })),
        },
    ],
    !!candidates,
  );

  // Empty-default capabilities use backend auto previews, sharing image-to-video with dropdown defaultAuto.
  const videoSpecBackend = currentVideo || auto?.i2v || "";
  // Global pages lack project context; tiers use server catalog derivation (unknown generation_mode always downgrades native),
  // never project-resolving /video-capabilities.
  const videoSpecTier = videoSpecBackend
    ? (lookupCatalogVideoAudio(providers, videoSpecBackend)?.voiceConsistency ?? null)
    : null;
  // Audio checkboxes inspect both effective buckets (empty subtiers use base defaults): both always-audible or
  // both always-silent means neither route gets switches; disable and show actual output tracks. Any controllable
  // bucket keeps controls enabled, avoiding idle base defaults disabling valid audio-off options.
  // Divergent buckets warn below, with one-click correction of existing off values; never silently rewrite settings
  // (enqueue preflight rejects actual execution buckets).
  // Read each bucket's own execution path: one model may differ (Kling v3-omni controls image-to-video audio
  // but lacks reference-video switches). Pathless values invent nonexistent r2v switches.
  const bucketAudioControl = (backend: string, route: VideoRoute) =>
    backend ? lookupVideoAudioControl(providers, backend, route) : null;
  // Empty layers execute automatically selected models; predicates use previews too.
  const i2vAudioControl = bucketAudioControl(currentVideoI2V || currentVideo || auto?.i2v || "", "i2v");
  const r2vAudioControl = bucketAudioControl(currentVideoR2V || currentVideo || auto?.r2v || "", "r2v");
  const audioLockedControl =
    i2vAudioControl === r2vAudioControl &&
    (i2vAudioControl === "always_on" || i2vAudioControl === "always_off")
      ? i2vAudioControl
      : null;
  const audioLocked = audioLockedControl !== null;
  const audioConflict =
    !currentAudio && (i2vAudioControl === "always_on" || r2vAudioControl === "always_on");
  const videoSpecDurations = videoSpecBackend
    ? catalogDurations(providers, customProviders, videoSpecBackend)
    : null;
  const videoSpecResolutions = videoSpecBackend
    ? lookupResolutions(providers, videoSpecBackend, customProviders, endpointToMediaType).options
    : [];

  // Omit defaultRoute: globals serve both paths, and either path misreports the other; absent project context
  // shows catalog i2v signals. Subtier dropdowns each use own buckets, matching i2vAudioControl / r2vAudioControl
  // above.
  const renderVideoOptionMeta = videoOptionMetaRenderer({ t, providers, customProviders, endpointToMediaType });
  const currentAudioBackend = fields.default_audio_backend ?? "";
  const currentNarrationVoice = fields.narration_voice ?? "";
  const currentNarrationSpeed =
    fields.narration_speed;

  // Global text tiers (docs/adr/0051): globals ground resolution; empty defaults mean inference, without inheritance.
  const currentTextDefault = fields.default_text_backend ?? "";
  const textTierValue = {
    default: currentTextDefault,
    simple: fields.text_backend_simple ?? "",
    complex: fields.text_backend_complex ?? "",
  };

  const lifecycle = candidates?.lifecycle;

  const candidatesSubFieldsError = candidatesError
    ? { onRetry: () => void reloadCandidates(), retrying: candidatesRetrying }
    : undefined;

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h2 className="text-lg font-medium">{t("settings_default_models")}</h2>
        <p className="text-sm text-muted-foreground">{t("model_selection_desc")}</p>
      </header>

      <ChannelCard title={t("default_models_channel_video")}>
        {videoBackends.length > 0 ? (
          <LayeredModelFields
            defaultLabel={t("default_video_model")}
            defaultValue={currentVideo}
            defaultOptions={videoBackends}
            onDefaultChange={(v) => setFields((prev) => ({ ...prev, default_video_backend: v }))}
            emptyLabel={t("auto_select")}
            emptyHint={t("auto")}
            // Defaults span both paths; context-free resolution uses image-to-video.
            defaultAuto={auto?.i2v}
            providerNames={allProviderNames}
            modelNames={allModelNames}
            renderOptionMeta={renderVideoOptionMeta}
            subFields={videoSubFields}
            subFieldsError={candidatesSubFieldsError}
            lifecycle={lifecycle}
          >
            {videoSpecBackend && (
              <VideoModelSpecBar
                durations={videoSpecDurations}
                resolutions={videoSpecResolutions}
                tier={videoSpecTier}
              />
            )}
          </LayeredModelFields>
        ) : (
          <NoProviders message={t("no_video_providers_hint")} />
        )}

        <div className="flex flex-col gap-2">
          <Label className="items-start">
            <Checkbox
              checked={audioLocked ? audioLockedControl === "always_on" : currentAudio}
              disabled={audioLocked}
              onCheckedChange={(checked) => setFields((prev) => ({ ...prev, video_generate_audio: checked }))}
            />
            <span className="flex flex-col gap-1">
              {t("generate_audio")}
              <span className="text-xs text-muted-foreground">
                {audioLocked
                  ? t(
                      audioLockedControl === "always_on"
                        ? "audio_switch_locked_always_on"
                        : "audio_switch_locked_always_off",
                    )
                  : t("audio_support_hint")}
              </span>
            </span>
          </Label>
          {audioConflict && (
            <InlineWarning
              message={t("audio_switch_conflict_notice")}
              action={{
                label: t("audio_switch_conflict_action"),
                onClick: () => setFields((prev) => ({ ...prev, video_generate_audio: true })),
              }}
            />
          )}
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="video-poll-timeout-input">{t("video_poll_timeout_label")}</Label>
          <Input
            id="video-poll-timeout-input"
            type="text"
            inputMode="decimal"
            aria-describedby="video-poll-timeout-hint"
            value={pollTimeoutInput ?? String(currentPollTimeout)}
            onChange={(e) => {
              const raw = e.target.value;
              setPollTimeoutInput(raw);
              const next = Number(raw);
              // Display raw strings, preserving "60."/empty states; valid numbers enter unsaved edits synchronously,
              // excluding empty/nonnumeric values so clearing invents no 0.
              if (raw.trim() !== "" && Number.isFinite(next)) {
                setFields((prev) => ({ ...prev, video_poll_timeout_seconds: next }));
              }
            }}
            onBlur={() => {
              if (pollTimeoutInput === null) return;
              const next = Number(pollTimeoutInput);
              // Blur normalization: truncate valid numbers into unsaved edits; empty/nonnumeric values withdraw that field's edits
              // including typed intermediate values, echoing saved values. Backend save validation enforces lower bounds.
              const restored =
                pollTimeoutInput.trim() !== "" && Number.isFinite(next)
                  ? Math.round(next)
                  : unit.savedValue.video_poll_timeout_seconds;
              setFields((prev) => ({ ...prev, video_poll_timeout_seconds: restored }));
              setPollTimeoutInput(null);
            }}
            className="w-40"
          />
          <FieldHint id="video-poll-timeout-hint">{t("video_poll_timeout_hint")}</FieldHint>
        </div>
      </ChannelCard>

      <ChannelCard title={t("default_models_channel_image")}>
        {imageBackends.length > 0 ? (
          <LayeredModelFields
            defaultLabel={t("default_image_model")}
            defaultValue={currentImage}
            defaultOptions={imageBackends}
            onDefaultChange={(v) => setFields((prev) => ({ ...prev, default_image_backend: v }))}
            emptyLabel={t("auto_select")}
            emptyHint={t("auto")}
            // Defaults span both paths; purpose-free resolution uses text-to-image.
            defaultAuto={auto?.t2i}
            providerNames={allProviderNames}
            modelNames={allModelNames}
            subFields={imageSubFields}
            subFieldsError={candidatesSubFieldsError}
            lifecycle={lifecycle}
          />
        ) : (
          <NoProviders message={t("no_image_providers_hint")} />
        )}
      </ChannelCard>

      <ChannelCard title={t("default_models_channel_text")} description={t("text_models_desc")}>
        {textBackends.length > 0 ? (
          <TextTierFields
            value={textTierValue}
            onChange={(next) =>
              setFields((prev) => ({
                ...prev,
                default_text_backend: next.default,
                text_backend_simple: next.simple,
                text_backend_complex: next.complex,
              }))
            }
            options={textBackends}
            providerNames={allProviderNames}
            modelNames={allModelNames}
            defaultLabel={t("auto_select")}
            defaultHint={t("auto")}
            fallbacks={{
              // Globals ground resolution: empty tiers use global defaults; empty defaults infer automatically.
              simple: currentTextDefault || undefined,
              complex: currentTextDefault || undefined,
            }}
            auto={auto?.text}
            lifecycle={lifecycle}
          />
        ) : (
          <NoProviders message={t("no_text_providers_hint")} />
        )}
      </ChannelCard>

      {/* Narration dubbing: model/voice/rate only prefill new TTS projects, explained at channel starts. */}
      <ChannelCard title={t("default_models_channel_audio")} description={t("global_tts_defaults_prefill_hint")}>
        {audioBackends.length > 0 ? (
          <div className="flex flex-col gap-3">
            <ProviderModelSelect
              value={currentAudioBackend}
              options={audioBackends}
              providerNames={allProviderNames}
              modelNames={allModelNames}
              onChange={(v) => setFields((prev) => ({ ...prev, default_audio_backend: v }))}
              allowDefault
              defaultLabel={t("auto_select")}
              defaultHint={t("auto")}
              aria-label={t("default_audio_model")}
            />
            <ModelLifecycleNotice
              value={currentAudioBackend}
              notices={lifecycle}
              modelNames={allModelNames}
              onSwitch={(v) => setFields((prev) => ({ ...prev, default_audio_backend: v }))}
            />
          </div>
        ) : (
          <NoProviders message={t("no_audio_providers_hint")} />
        )}

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="narration-voice-input">{t("narration_voice_label")}</Label>
          <NarrationVoiceInput
            id="narration-voice-input"
            aria-describedby="narration-voice-hint"
            value={currentNarrationVoice}
            onChange={(narration_voice) => setFields((prev) => ({ ...prev, narration_voice }))}
            voices={audioVoices}
          />
          <FieldHint id="narration-voice-hint">
            {audioVoices.length > 0 ? t("narration_voice_hint_with_voices") : t("narration_voice_hint")}
          </FieldHint>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="narration-speed-input">{t("narration_speed_label")}</Label>
          <Input
            id="narration-speed-input"
            type="number"
            min={0.1}
            step={0.1}
            aria-describedby="narration-speed-hint"
            value={currentNarrationSpeed ?? ""}
            onChange={(e) => {
              const raw = e.target.value;
              setFields((prev) => {
                if (raw === "") return { ...prev, narration_speed: null };
                const next = Number(raw);
                // Filter nonfinite only: JSON serializes NaN/Infinity as null, accidentally clearing values.
                // Permit temporary 0/negatives (typing 0.5 passes through 0); backend save validation enforces positivity.
                if (!Number.isFinite(next)) return prev;
                return { ...prev, narration_speed: next };
              });
            }}
            className="w-40"
          />
          <FieldHint id="narration-speed-hint">{t("narration_speed_hint")}</FieldHint>
        </div>
      </ChannelCard>

      <PageShellFooter>
        <SaveBar unit={unit} />
      </PageShellFooter>
    </div>
  );
}
