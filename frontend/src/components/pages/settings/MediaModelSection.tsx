
import { useState, useEffect, useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { useWarnUnsaved } from "@/hooks/useWarnUnsaved";
import { API } from "@/api";
import type {
  SystemConfigSettings,
  SystemConfigOptions,
  SystemConfigPatch,
} from "@/types/system";
import type { CustomProviderInfo } from "@/types/custom-provider";
import { ProviderModelSelect } from "@/components/ui/ProviderModelSelect";
import {
  LayeredModelFields,
  degradeSubFieldsToSaved,
  useCapabilityBucketLabels,
  type LayeredSubField,
} from "@/components/shared/LayeredModelFields";
import { TextTierFields } from "@/components/shared/TextTierFields";
import { VideoModelSpecBar, videoOptionMetaRenderer } from "@/components/shared/VideoModelSpecBar";
import { InlineWarning } from "@/components/ui/InlineWarning";
import { PROVIDER_NAMES } from "@/components/ui/ProviderIcon";
import { useAppStore } from "@/stores/app-store";
import { useCapabilitiesStore } from "@/stores/capabilities-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import { catalogDurations } from "@/hooks/useModelCapabilities";
import { useModelCandidates } from "@/hooks/useModelCandidates";
import { errMsg } from "@/utils/async";
import {
  getCustomProviderModels,
  getProviderModels,
  lookupCatalogVideoAudio,
  lookupResolutions,
  lookupVideoAudioControl,
} from "@/utils/provider-models";
import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE, CARD_STYLE } from "@/components/ui/darkroom-tokens";
import type { ProviderInfo, VideoRoute } from "@/types/provider";

interface CardProps {
  kicker: string;
  title?: string;
  description?: string;
  children: React.ReactNode;
}

function SectionCard({ kicker, title, description, children }: CardProps) {
  return (
    <div
      className="rounded-[10px] border border-hairline p-5"
      style={CARD_STYLE}
    >
      <div className="mb-4">
        <div className="font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-accent-2">
          {kicker}
        </div>
        {title && (
          <h4 className="mt-1.5 text-[14px] font-medium text-text">{title}</h4>
        )}
        {description && (
          <p className="mt-1 text-[12px] leading-[1.55] text-text-3">{description}</p>
        )}
      </div>
      {children}
    </div>
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
  const [draft, setDraft] = useState<SystemConfigPatch>({});
  const [saving, setSaving] = useState(false);

  const isDirty = Object.keys(draft).length > 0;
  useWarnUnsaved(isDirty);

  const endpointToMediaType = useEndpointCatalogStore((s) => s.endpointToMediaType);
  const fetchEndpointCatalog = useEndpointCatalogStore((s) => s.fetch);
  useEffect(() => {
    if (customProviders.length > 0) void fetchEndpointCatalog();
  }, [customProviders.length, fetchEndpointCatalog]);

  const allProviderNames = useMemo(
    () => ({ ...PROVIDER_NAMES, ...(options?.provider_names ?? {}) }),
    [options],
  );
  const bucketLabels = useCapabilityBucketLabels();

  // 候选与其余配置分开拉：它自带失败态，失败时只影响细分区、不牵动已加载的表单状态，
  // 也让重试不必重取整页配置（会连带清空未保存的 draft）。启动后不等它落地——候选接口
  // 慢或悬挂时，整页 spinner 和保存流程都会跟着卡住，而细分区本就有自己的加载叙事。
  const fetchConfig = useCallback(async () => {
    void reloadCandidates();
    const [res, catalog, custom] = await Promise.all([
      API.getSystemConfig(),
      getProviderModels().catch(() => [] as ProviderInfo[]),
      getCustomProviderModels().catch(() => [] as CustomProviderInfo[]),
    ]);
    setSettings(res.settings);
    setOptions(res.options);
    setProviders(catalog);
    setCustomProviders(custom);
    setDraft({});
  }, [reloadCandidates]);

  useEffect(() => {
    // mount/依赖变更时异步拉取配置，回调内 setSettings 等（异步 fetch 后回写）
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void fetchConfig();
  }, [fetchConfig]);

  const handleSave = useCallback(async () => {
    if (Object.keys(draft).length === 0) return;
    setSaving(true);
    try {
      await API.updateSystemConfig(draft);
      // 全局默认视频后端参与项目能力的三级解析（项目 > 系统设置 > 系统默认）。项目未指定
      // 后端时改这里会换掉生效模型，而项目字段一个都没变、在用的能力查询不会因 props 重取。
      useCapabilitiesStore.getState().invalidate();
      await fetchConfig();
      void useConfigStatusStore.getState().refresh();
      useAppStore.getState().pushToast(t("media_config_saved"), "success");
    } catch (err) {
      useAppStore.getState().pushToast(t("save_failed", { message: errMsg(err) }), "error");
    } finally {
      setSaving(false);
    }
  }, [draft, fetchConfig, t]);

  if (!settings || !options) {
    return (
      <div className="flex items-center gap-2 px-1 py-12 text-text-3">
        <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin text-accent-2" aria-hidden />
        <span className="font-mono text-[11px] uppercase tracking-[0.14em]">
          {t("common:loading")}
        </span>
      </div>
    );
  }

  const videoBackends: string[] = options.video_backends ?? [];
  const imageBackends: string[] = options.image_backends ?? [];
  const textBackends: string[] = options.text_backends ?? [];
  const audioBackends: string[] = options.audio_backends ?? [];

  const currentVideo = draft.default_video_backend ?? settings.default_video_backend ?? "";
  const currentVideoI2V = draft.default_video_backend_i2v ?? settings.default_video_backend_i2v ?? "";
  const currentVideoR2V = draft.default_video_backend_r2v ?? settings.default_video_backend_r2v ?? "";
  const currentImage = draft.default_image_backend ?? settings.default_image_backend ?? "";
  const currentImageT2I = draft.default_image_backend_t2i ?? settings.default_image_backend_t2i ?? "";
  const currentImageI2I = draft.default_image_backend_i2i ?? settings.default_image_backend_i2i ?? "";
  const currentAudio = draft.video_generate_audio ?? settings.video_generate_audio ?? false;
  const currentPollTimeout =
    draft.video_poll_timeout_seconds ?? settings.video_poll_timeout_seconds;

  // 全局层是解析链的基准，细分项留空即回退全局默认模型；默认模型也留空时是自动推断，
  // 前端算不出具体模型，故不显示生效值（下拉里显示「自动选择」）。
  const videoSubFields: LayeredSubField[] = degradeSubFieldsToSaved(
    [
        {
          key: "i2v",
          ...bucketLabels.i2v,
          value: currentVideoI2V,
          options: candidates?.video.buckets.i2v ?? [],
          effective: currentVideo || undefined,
          onChange: (v) => setDraft((prev) => ({ ...prev, default_video_backend_i2v: v })),
        },
        {
          key: "r2v",
          ...bucketLabels.r2v,
          value: currentVideoR2V,
          options: candidates?.video.buckets.r2v ?? [],
          effective: currentVideo || undefined,
          onChange: (v) => setDraft((prev) => ({ ...prev, default_video_backend_r2v: v })),
        },
    ],
    !!candidates,
  );

  const imageSubFields: LayeredSubField[] = degradeSubFieldsToSaved(
    [
        {
          key: "t2i",
          ...bucketLabels.t2i,
          value: currentImageT2I,
          options: candidates?.image.buckets.t2i ?? [],
          effective: currentImage || undefined,
          onChange: (v) => setDraft((prev) => ({ ...prev, default_image_backend_t2i: v })),
        },
        {
          key: "i2i",
          ...bucketLabels.i2i,
          value: currentImageI2I,
          options: candidates?.image.buckets.i2i ?? [],
          effective: currentImage || undefined,
          onChange: (v) => setDraft((prev) => ({ ...prev, default_image_backend_i2i: v })),
        },
    ],
    !!candidates,
  );

  // 全局设置页无项目上下文，档位读目录端点的服务端派生值（generation_mode 未知，native 恒降格），
  // 不打 /video-capabilities——该端点按项目解析。
  const videoSpecTier = currentVideo
    ? (lookupCatalogVideoAudio(providers, currentVideo)?.voiceConsistency ?? null)
    : null;
  // 音频勾选框按两个生效桶（细分桶留空即回退基础默认）的开关可控性判定：两桶同为恒有声或
  // 同为恒无声时，无论选择哪种生成模式都收不到音轨开关，置灰并展示成片的实际音轨状态。只要还有一个
  // 桶可控就不置灰——否则闲置的基础默认会连带禁掉可控桶的合法关闭。
  // 两桶不一致时只由下方警告提示，存量的「关闭」由警告给一键修正入口，不静默改写配置
  // （入队前预检按实际执行的桶拒绝）。
  // 每个桶按它自己的执行路径取值：同一模型在两条路径上的音轨形态可以不同（可灵 v3-omni 图生
  // 可控、参考生无开关），按无路径上下文的值取会让 r2v 桶报出一个执行期不存在的开关。
  const bucketAudioControl = (backend: string, route: VideoRoute) =>
    backend ? lookupVideoAudioControl(providers, backend, route) : null;
  const i2vAudioControl = bucketAudioControl(currentVideoI2V || currentVideo, "i2v");
  const r2vAudioControl = bucketAudioControl(currentVideoR2V || currentVideo, "r2v");
  const audioLockedControl =
    i2vAudioControl === r2vAudioControl &&
    (i2vAudioControl === "always_on" || i2vAudioControl === "always_off")
      ? i2vAudioControl
      : null;
  const audioLocked = audioLockedControl !== null;
  const audioConflict =
    !currentAudio && (i2vAudioControl === "always_on" || r2vAudioControl === "always_on");
  const videoSpecDurations = currentVideo ? catalogDurations(providers, customProviders, currentVideo) : null;
  const videoSpecResolutions = currentVideo
    ? lookupResolutions(providers, currentVideo, customProviders, endpointToMediaType).options
    : [];

  // 不传 defaultRoute：全局默认模型两条路径都会用到，取任一条都会误报另一条；无项目上下文
  // 时按目录 i2v 位展示。两个细分项下拉各按自己的桶取值，与上方 i2vAudioControl / r2vAudioControl
  // 同口径。
  const renderVideoOptionMeta = videoOptionMetaRenderer({ t, providers, customProviders, endpointToMediaType });
  const currentAudioBackend = draft.default_audio_backend ?? settings.default_audio_backend ?? "";
  const currentNarrationVoice = draft.narration_voice ?? settings.narration_voice ?? "";
  const currentNarrationSpeed =
    "narration_speed" in draft ? draft.narration_speed : settings.narration_speed;

  // 全局文本档位（docs/adr/0051）：全局是解析链基准，默认模型也留空即自动推断（无继承来源）。
  const currentTextDefault = draft.default_text_backend ?? settings.default_text_backend ?? "";
  const textTierValue = {
    default: currentTextDefault,
    simple: draft.text_backend_simple ?? settings.text_backend_simple ?? "",
    complex: draft.text_backend_complex ?? settings.text_backend_complex ?? "",
  };

  const candidatesSubFieldsError = candidatesError
    ? { onRetry: () => void reloadCandidates(), retrying: candidatesRetrying }
    : undefined;

  const emptyHint = (msg: string) => (
    <div className="rounded-[8px] border border-hairline-soft bg-bg-grad-a/45 px-3 py-2.5 text-[12px] text-text-3">
      {msg}
    </div>
  );

  return (
    <div className="space-y-7">
      {/* Heading */}
      <div>
        <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-accent-2">
          Default Routing
        </div>
        <h3
          className="font-editorial mt-1"
          style={{
            fontWeight: 400,
            fontSize: 22,
            lineHeight: 1.1,
            letterSpacing: "-0.012em",
            color: "var(--color-text)",
          }}
        >
          {t("model_selection")}
        </h3>
        <p className="mt-1.5 text-[12.5px] leading-[1.6] text-text-3">
          {t("model_selection_desc")}
        </p>
      </div>

      {/* Video */}
      <SectionCard kicker="Video Channel" title={t("default_video_model")}>
        {videoBackends.length > 0 ? (
          <LayeredModelFields
            defaultLabel={t("default_video_model")}
            defaultValue={currentVideo}
            defaultOptions={videoBackends}
            onDefaultChange={(v) => setDraft((prev) => ({ ...prev, default_video_backend: v }))}
            emptyLabel={t("auto_select")}
            emptyHint={t("auto")}
            providerNames={allProviderNames}
            renderOptionMeta={renderVideoOptionMeta}
            subFields={videoSubFields}
            subFieldsError={candidatesSubFieldsError}
          >
            {currentVideo && (
              <VideoModelSpecBar
                durations={videoSpecDurations}
                resolutions={videoSpecResolutions}
                tier={videoSpecTier}
              />
            )}
          </LayeredModelFields>
        ) : (
          emptyHint(t("no_video_providers_hint"))
        )}

        <div
          className={`mt-4 flex items-start gap-2.5 text-[12.5px] ${
            audioLocked ? "text-text-4" : "text-text-2"
          }`}
        >
          <input
            id="media-generate-audio"
            type="checkbox"
            checked={audioLocked ? audioLockedControl === "always_on" : currentAudio}
            disabled={audioLocked}
            onChange={(e) =>
              setDraft((prev) => ({ ...prev, video_generate_audio: e.target.checked }))
            }
            className="mt-0.5 h-3.5 w-3.5 rounded border-hairline bg-bg-grad-a accent-[var(--color-accent)] disabled:cursor-not-allowed enabled:cursor-pointer"
          />
          <label
            htmlFor="media-generate-audio"
            className={`flex flex-col ${audioLocked ? "cursor-not-allowed" : "cursor-pointer"}`}
          >
            <span>{t("generate_audio")}</span>
            <span className="text-[11px] text-text-4">
              {audioLocked
                ? t(
                    audioLockedControl === "always_on"
                      ? "audio_switch_locked_always_on"
                      : "audio_switch_locked_always_off",
                  )
                : t("audio_support_hint")}
            </span>
          </label>
        </div>
        {audioConflict && (
          <InlineWarning
            className="mt-2"
            message={t("audio_switch_conflict_notice")}
            action={{
              label: t("audio_switch_conflict_action"),
              onClick: () => setDraft((prev) => ({ ...prev, video_generate_audio: true })),
            }}
          />
        )}
        <div className="mt-4">
          <label
            htmlFor="video-poll-timeout-input"
            className="mb-1.5 block font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4"
          >
            {t("video_poll_timeout_label")}
          </label>
          <input
            id="video-poll-timeout-input"
            type="number"
            min={60}
            step={1}
            value={currentPollTimeout}
            onChange={(e) => {
              const next = Number(e.target.value);
              if (Number.isInteger(next)) {
                setDraft((prev) => ({ ...prev, video_poll_timeout_seconds: next }));
              }
            }}
            className="w-full rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-2 text-[12.5px] text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          />
          <p className="mt-1 text-[11px] text-text-4">{t("video_poll_timeout_hint")}</p>
        </div>
      </SectionCard>

      {/* Image */}
      <SectionCard kicker="Image Channel" title={t("default_image_model")}>
        {imageBackends.length > 0 ? (
          <LayeredModelFields
            defaultLabel={t("default_image_model")}
            defaultValue={currentImage}
            defaultOptions={imageBackends}
            onDefaultChange={(v) => setDraft((prev) => ({ ...prev, default_image_backend: v }))}
            emptyLabel={t("auto_select")}
            emptyHint={t("auto")}
            providerNames={allProviderNames}
            subFields={imageSubFields}
            subFieldsError={candidatesSubFieldsError}
          />
        ) : (
          emptyHint(t("no_image_providers_hint"))
        )}
      </SectionCard>

      {/* Text */}
      <SectionCard kicker="Text Channel" title={t("text_models")} description={t("text_models_desc")}>
        {textBackends.length > 0 ? (
          <TextTierFields
            value={textTierValue}
            onChange={(next) =>
              setDraft((prev) => ({
                ...prev,
                default_text_backend: next.default,
                text_backend_simple: next.simple,
                text_backend_complex: next.complex,
              }))
            }
            options={textBackends}
            providerNames={allProviderNames}
            defaultLabel={t("auto_select")}
            defaultHint={t("auto")}
            fallbacks={{
              // 全局层是解析链基准：各档留空即回退全局默认模型；默认模型也空时是自动推断。
              simple: currentTextDefault || undefined,
              complex: currentTextDefault || undefined,
            }}
          />
        ) : (
          emptyHint(t("no_text_providers_hint"))
        )}
      </SectionCard>

      {/* Audio (narration TTS) */}
      <SectionCard kicker="Audio Channel" title={t("default_audio_model")}>
        {audioBackends.length > 0 ? (
          <ProviderModelSelect
            value={currentAudioBackend}
            options={audioBackends}
            providerNames={allProviderNames}
            onChange={(v) => setDraft((prev) => ({ ...prev, default_audio_backend: v }))}
            allowDefault
            defaultLabel={t("auto_select")}
            defaultHint={t("auto")}
            aria-label={t("default_audio_model")}
          />
        ) : (
          emptyHint(t("no_audio_providers_hint"))
        )}

        <div className="mt-4 space-y-3.5">
          <div>
            <label
              htmlFor="narration-voice-input"
              className="mb-1.5 block font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4"
            >
              {t("narration_voice_label")}
            </label>
            <input
              id="narration-voice-input"
              type="text"
              value={currentNarrationVoice}
              onChange={(e) => setDraft((prev) => ({ ...prev, narration_voice: e.target.value }))}
              className="w-full rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-2 text-[12.5px] text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            />
            <p className="mt-1 text-[11px] text-text-4">{t("narration_voice_hint")}</p>
          </div>
          <div>
            <label
              htmlFor="narration-speed-input"
              className="mb-1.5 block font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4"
            >
              {t("narration_speed_label")}
            </label>
            <input
              id="narration-speed-input"
              type="number"
              min={0.1}
              step={0.1}
              value={currentNarrationSpeed ?? ""}
              onChange={(e) => {
                const raw = e.target.value;
                setDraft((prev) => {
                  if (raw === "") return { ...prev, narration_speed: null };
                  const next = Number(raw);
                  // 仅过滤非有限数：NaN/Infinity 会被 JSON 序列化为 null 误触"清除"语义。
                  // 0/负数允许临时存在（键入 0.5 会先经过 0），正数约束由保存时后端校验兜底。
                  if (!Number.isFinite(next)) return prev;
                  return { ...prev, narration_speed: next };
                });
              }}
              className="w-full rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-2 text-[12.5px] text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            />
            <p className="mt-1 text-[11px] text-text-4">{t("narration_speed_hint")}</p>
          </div>
        </div>
      </SectionCard>

      {/* Footer */}
      {isDirty && (
        <div className="flex gap-2 pt-1">
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving}
            className={ACCENT_BTN_CLS}
            style={ACCENT_BUTTON_STYLE}
          >
            {saving ? (
              <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden />
            ) : null}
            {saving ? t("common:saving") : t("common:save")}
          </button>
          <button
            type="button"
            onClick={() => setDraft({})}
            className="rounded-[8px] border border-hairline bg-bg-grad-a/55 px-4 py-2 text-[12.5px] text-text-2 transition-colors hover:border-hairline-strong hover:bg-bg-grad-a hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            {t("common:reset")}
          </button>
        </div>
      )}
    </div>
  );
}
