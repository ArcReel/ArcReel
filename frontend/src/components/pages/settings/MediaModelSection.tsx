
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
  SystemConfigSettings,
  SystemConfigOptions,
  SystemConfigPatch,
} from "@/types/system";
import type { CustomProviderInfo } from "@/types/custom-provider";
import { ProviderModelSelect } from "@/components/shared/ProviderModelSelect";
import {
  LayeredModelFields,
  degradeSubFieldsToSaved,
  useGenerationTypeBucketLabels,
  type LayeredSubField,
} from "@/components/shared/LayeredModelFields";
import { TextTierFields } from "@/components/shared/TextTierFields";
import { VideoModelSpecBar, videoOptionMetaRenderer } from "@/components/shared/VideoModelSpecBar";
import { InlineWarning } from "@/components/shared/InlineWarning";
import { SaveBar } from "@/components/shared/edit-unit/SaveBar";
import { PageShellFooter } from "@/components/shared/page-shell/PageShell";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { useCapabilitiesStore } from "@/stores/capabilities-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import { useDisplayNames } from "@/hooks/useDisplayNames";
import { useModelCandidates } from "@/hooks/useModelCandidates";
import {
  catalogDurations,
  getCustomProviderModels,
  getProviderModels,
  lookupCatalogVideoAudio,
  lookupResolutions,
  lookupVideoAudioControl,
} from "@/utils/provider-models";
import type { ProviderInfo, VideoRoute } from "@/types/provider";

/** 本页编辑单元包含的系统设置字段；保存时只提交改过的字段。 */
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

/** 一个通道一张卡片：标题、可选说明，然后是字段。 */
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

/** 通道没有可选模型时的空状态，链接到「供应商」去配置。 */
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
  // 轮询超时编辑期的原始字符串（null = 未在编辑）：受控 value 若直接取数字，
  // 「60.」等中间态与清空会被数字化吞掉；失焦时统一解析写回未保存修改。
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

  // 候选与其余配置分开拉：它自带失败态，失败时只影响细分区、不牵动已加载的表单状态，
  // 也让重试不必重取整页配置。启动后不等它落地——候选接口
  // 慢或悬挂时，整页 spinner 和保存流程都会跟着卡住，而细分区本就有自己的加载叙事。
  const fetchConfig = useCallback(async () => {
    const [res, catalog, custom] = await Promise.all([
      API.getSystemConfig(),
      getProviderModels().catch(() => [] as ProviderInfo[]),
      getCustomProviderModels().catch(() => [] as CustomProviderInfo[]),
    ]);
    setSettings(res.settings);
    setOptions(res.options);
    setProviders(catalog);
    setCustomProviders(custom);
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 依赖变更时异步拉取配置后回写
    void fetchConfig();
  }, [fetchConfig]);

  // 候选独立于配置本体重取：reload 的标识随语言变化，故语言切换时只刷新候选与译名，
  // 不走 fetchConfig（重取整页配置没有必要）。
  useEffect(() => {
    void reloadCandidates();
  }, [reloadCandidates]);

  const source = useMemo(() => fieldsFrom(settings), [settings]);
  const saveFields = useCallback(
    async (fields: MediaModelFields, saved: MediaModelFields) => {
      const res = await API.updateSystemConfig(changedFields(fields, saved));
      // 全局默认视频后端参与项目能力的三级解析（项目 > 系统设置 > 系统默认）。项目未指定
      // 后端时改这里会换掉生效模型，而项目字段一个都没变、在用的能力查询不会因 props 重取。
      useCapabilitiesStore.getState().invalidate();
      await fetchConfig();
      void reloadCandidates();
      void useConfigStatusStore.getState().refresh();
      // 以服务端规范化后的值（如去掉首尾空白的旁白音色）作为已保存内容，不依赖重取配置的渲染时机
      return fieldsFrom(res.settings);
    },
    [fetchConfig, reloadCandidates],
  );
  const unit = useEditUnit({ source, save: saveFields });
  const fields = unit.value;
  const setFields = unit.setValue;

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
          onChange: (v) => setFields((prev) => ({ ...prev, default_video_backend_i2v: v })),
        },
        {
          key: "r2v",
          ...bucketLabels.r2v,
          value: currentVideoR2V,
          options: candidates?.video.buckets.r2v ?? [],
          effective: currentVideo || undefined,
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
          value: currentImageT2I,
          options: candidates?.image.buckets.t2i ?? [],
          effective: currentImage || undefined,
          onChange: (v) => setFields((prev) => ({ ...prev, default_image_backend_t2i: v })),
        },
        {
          key: "i2i",
          ...bucketLabels.i2i,
          value: currentImageI2I,
          options: candidates?.image.buckets.i2i ?? [],
          effective: currentImage || undefined,
          onChange: (v) => setFields((prev) => ({ ...prev, default_image_backend_i2i: v })),
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
  const currentAudioBackend = fields.default_audio_backend ?? "";
  const currentNarrationVoice = fields.narration_voice ?? "";
  const currentNarrationSpeed =
    fields.narration_speed;

  // 全局文本档位（docs/adr/0051）：全局是解析链基准，默认模型也留空即自动推断（无继承来源）。
  const currentTextDefault = fields.default_text_backend ?? "";
  const textTierValue = {
    default: currentTextDefault,
    simple: fields.text_backend_simple ?? "",
    complex: fields.text_backend_complex ?? "",
  };

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
            providerNames={allProviderNames}
            modelNames={allModelNames}
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
              // 显示以原始字符串为准（「60.」等中间态与清空保真）；有效数值同步进未保存修改，
              // 空串或非数值不写入——清空不会产生 0 这类假值。
              if (raw.trim() !== "" && Number.isFinite(next)) {
                setFields((prev) => ({ ...prev, video_poll_timeout_seconds: next }));
              }
            }}
            onBlur={() => {
              if (pollTimeoutInput === null) return;
              const next = Number(pollTimeoutInput);
              // 失焦归一：有效数值取整写入未保存修改；空串或非数值撤销该字段的未保存编辑
              // （连同键入过程写入的中间值），回显已保存值。下限由保存时后端校验兜底。
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
            providerNames={allProviderNames}
            modelNames={allModelNames}
            subFields={imageSubFields}
            subFieldsError={candidatesSubFieldsError}
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
              // 全局层是解析链基准：各档留空即回退全局默认模型；默认模型也空时是自动推断。
              simple: currentTextDefault || undefined,
              complex: currentTextDefault || undefined,
            }}
          />
        ) : (
          <NoProviders message={t("no_text_providers_hint")} />
        )}
      </ChannelCard>

      {/* 旁白配音：模型、音色与语速只是新建 TTS 配音项目的预填值，说明放在通道开头。 */}
      <ChannelCard title={t("default_models_channel_audio")} description={t("global_tts_defaults_prefill_hint")}>
        {audioBackends.length > 0 ? (
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
        ) : (
          <NoProviders message={t("no_audio_providers_hint")} />
        )}

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="narration-voice-input">{t("narration_voice_label")}</Label>
          <Input
            id="narration-voice-input"
            type="text"
            aria-describedby="narration-voice-hint"
            value={currentNarrationVoice}
            onChange={(e) => setFields((prev) => ({ ...prev, narration_voice: e.target.value }))}
          />
          <FieldHint id="narration-voice-hint">{t("narration_voice_hint")}</FieldHint>
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
                // 仅过滤非有限数：NaN/Infinity 会被 JSON 序列化为 null 误触"清除"语义。
                // 0/负数允许临时存在（键入 0.5 会先经过 0），正数约束由保存时后端校验兜底。
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
