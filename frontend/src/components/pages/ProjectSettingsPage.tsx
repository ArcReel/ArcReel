import { useParams, useLocation } from "wouter";
import { errMsg, voidCall } from "@/utils/async";
import { useState, useEffect, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { API, type AgentProfileStatus } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useCapabilitiesStore } from "@/stores/capabilities-store";
import { getProviderModels, getCustomProviderModels } from "@/utils/provider-models";
import { ModelConfigSection } from "@/components/shared/ModelConfigSection";
import { executingImageModel, executingVideoModel } from "@/components/shared/LayeredModelFields";
import {
  NarrationDeliveryFields,
  narrationDeliveryProblem,
  type NarrationDeliveryValue,
} from "@/components/shared/NarrationDeliveryFields";
import { type StylePickerValue } from "@/components/shared/StylePicker";
import { DEFAULT_TEMPLATE_ID, STYLE_TEMPLATES } from "@/data/style-templates";
import type {
  CharacterVoiceBinding,
  CustomProviderInfo,
  NarrationDefaultsResponse,
  NarrationDelivery,
  ProviderInfo,
} from "@/types";
import { DEFAULT_CHARACTER_VOICE_BINDING } from "@/types";
import { useDisplayNames } from "@/hooks/useDisplayNames";
import { useModelCandidates } from "@/hooks/useModelCandidates";
import { ROUTE_META, RouteLockBadge } from "@/components/shared/GenerationRouteCards";
import { GridStoryboardBar } from "@/components/shared/GridStoryboardBar";
import {
  EpisodeTargetDurationField,
  isValidEpisodeTargetDuration,
} from "@/components/shared/EpisodeTargetDurationField";
import { AdTargetDurationField } from "@/components/shared/AdTargetDurationField";
import { SpeechRateField, isValidSpeechRate } from "@/components/shared/SpeechRateField";
import { radioCardClass } from "@/components/ui/darkroom-tokens";
import { Button } from "@/components/ui/button";
import { AgentMemoryCabinet } from "@/components/agent/AgentMemoryCabinet";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useWarnUnsaved } from "@/hooks/useWarnUnsaved";
import { normalizeRoute, type GenerationRoute } from "@/utils/generation-mode";
import { getProjectDisplayName } from "@/utils/project-display";
import { effectiveModel } from "@/components/shared/LayeredModelFields";
import { useProjectsStore } from "@/stores/projects-store";
import { LayoutA, LayoutB, LayoutC, type PsLayoutProps } from "@/prototype/project-settings/layouts";
import { StyleField, UnsavedGuardDialog, type PsGroup, type PsSummaryRow } from "@/prototype/project-settings/parts";
import { usePsParams } from "@/prototype/project-settings/ps-variant";

function deriveStyleValue(project: Record<string, unknown>, projectName: string): StylePickerValue {
  const styleImage = project.style_image as string | undefined;
  const templateId = (project.style_template_id as string | undefined) ?? null;
  if (styleImage) {
    return {
      mode: "custom",
      templateId: null,
      activeCategory: "live",
      uploadedFile: null,
      uploadedPreview: `/api/v1/files/${encodeURIComponent(projectName)}/${styleImage}`,
    };
  }
  const effectiveId = templateId ?? DEFAULT_TEMPLATE_ID;
  const tpl = STYLE_TEMPLATES.find((x) => x.id === effectiveId);
  return {
    mode: "template",
    templateId: effectiveId,
    activeCategory: tpl?.category ?? "live",
    uploadedFile: null,
    uploadedPreview: null,
  };
}

function sameProfileFiles(left: string[], right: string[]) {
  return left.length === right.length && left.every((file, index) => file === right[index]);
}

// ─── Component ───────────────────────────────────────────────────────────────

export function ProjectSettingsPage() {
  const { t } = useTranslation("dashboard");
  const params = useParams<{ projectName: string }>();
  // PROTOTYPE #2971：变体 C 嵌在工作区的 nest 路由里，参数取不到，改从当前项目取
  const nestedProjectName = useProjectsStore((s) => s.currentProjectName);
  const projectName = params.projectName || nestedProjectName || "";
  const [reloadKey, setReloadKey] = useState(0);
  const [justSaved, setJustSaved] = useState(false);
  const [, navigate] = useLocation();

  const [options, setOptions] = useState<{
    video_backends: string[];
    image_backends: string[];
    text_backends: string[];
    audio_backends: string[];
    provider_names?: Record<string, string>;
    model_names?: Record<string, string>;
  } | null>(null);
  const {
    candidates,
    error: candidatesError,
    retrying: candidatesRetrying,
    reload: reloadCandidates,
  } = useModelCandidates();
  const [globalDefaults, setGlobalDefaults] = useState<{
    video: string;
    videoI2V: string;
    videoR2V: string;
    image: string;
    imageT2I: string;
    imageI2I: string;
    textDefault: string;
    textSimple: string;
    textComplex: string;
    audio: string;
  }>({
    video: "", videoI2V: "", videoR2V: "",
    image: "", imageT2I: "", imageI2I: "",
    textDefault: "", textSimple: "", textComplex: "", audio: "",
  });
  // 全局「生成有声视频」的生效值，供项目级「跟随全局」时判定与执行模型的矛盾。
  // 未保存过时取 true，镜像后端 _DEFAULT_VIDEO_GENERATE_AUDIO。
  const [globalGenerateAudio, setGlobalGenerateAudio] = useState(true);

  // Project-level overrides (from project.json)
  // "" means "follow global default"
  const [videoBackend, setVideoBackend] = useState<string>("");
  const [videoProviderI2V, setVideoProviderI2V] = useState<string>("");
  const [videoProviderR2V, setVideoProviderR2V] = useState<string>("");
  const [imageBackendDefault, setImageBackendDefault] = useState<string>("");
  const [imageBackendT2I, setImageBackendT2I] = useState<string>("");
  const [imageBackendI2I, setImageBackendI2I] = useState<string>("");
  const [audioOverride, setAudioOverride] = useState<boolean | null>(null);
  // 旁白交付方式与 TTS 快照（docs/adr/0089）：快照不跟随全局默认，切到后期配音时保留
  const [narrationDelivery, setNarrationDelivery] = useState<NarrationDelivery>("post_production");
  const [audioBackend, setAudioBackend] = useState<string>("");
  const [narrationVoice, setNarrationVoice] = useState<string>("");
  const [narrationSpeed, setNarrationSpeed] = useState<number | null>(null);
  // 全局默认的 TTS 设置：没有快照的项目切到 TTS 配音时用它预填
  const [narrationDefaults, setNarrationDefaults] = useState<NarrationDefaultsResponse | null>(null);
  // 角色声音绑定方式：参考生视频路线专有；缺省即默认档（提示词软约束）
  const [voiceBinding, setVoiceBinding] = useState<CharacterVoiceBinding>(DEFAULT_CHARACTER_VOICE_BINDING);
  const [textDefault, setTextDefault] = useState<string>("");
  const [textSimple, setTextSimple] = useState<string>("");
  const [textComplex, setTextComplex] = useState<string>("");
  const [aspectRatio, setAspectRatio] = useState<string>("");
  // 生成模式创建时锁定，此页只读展示；宫格装配开关随时可切
  const [generationRoute, setGenerationRoute] = useState<GenerationRoute>("storyboard");
  const [gridStoryboard, setGridStoryboard] = useState(false);
  const [defaultDuration, setDefaultDuration] = useState<number | null>(null);
  // 口播语速估算（阅读单位 / 秒）：null = 未填，按项目语言的默认速度估算
  const [speechRate, setSpeechRate] = useState<number | null>(null);
  const [episodeTargetDuration, setEpisodeTargetDuration] = useState<number | null>(null);
  // ad 项目的目标总时长（秒）；自定义输入不是正整数时为 null，拦住保存。
  const [adTargetDuration, setAdTargetDuration] = useState<number | null>(null);
  // 每次从项目载入目标总时长时换一次 key，让档位控件按载入值重新初始化。
  const [adTargetLoadCount, setAdTargetLoadCount] = useState(0);
  // 源文语言由内容分析写入，此页只读——只用来决定语速的单位名词（字 / 词）
  const [sourceLanguage, setSourceLanguage] = useState<string | null>(null);
  const [videoResolutions, setVideoResolutions] = useState<Record<string, string | null>>({});
  const [imageResolution, setImageResolution] = useState<string | null>(null);
  const [modelSettings, setModelSettings] = useState<Record<string, { resolution: string | null }>>({});
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [customProviders, setCustomProviders] = useState<CustomProviderInfo[]>([]);

  const { providerNames: allProviderNames, modelNames: allModelNames } = useDisplayNames(
    providers,
    customProviders,
    options,
    candidates,
  );

  const [projectTitle, setProjectTitle] = useState<string>("");
  const [contentMode, setContentMode] = useState<string>("narration");
  const [saving, setSaving] = useState(false);
  const [loadedAgentProfile, setLoadedAgentProfile] = useState<{
    projectName: string;
    status: AgentProfileStatus;
  } | null>(null);
  const agentProfile = loadedAgentProfile?.projectName === projectName ? loadedAgentProfile.status : null;
  const [profileResetProject, setProfileResetProject] = useState<string | null>(null);
  const [profileResetting, setProfileResetting] = useState(false);

  // ── Style picker state (independent save flow) ─────────────────────────────
  const [styleValue, setStyleValue] = useState<StylePickerValue | null>(null);
  const initialRef = useRef({
    videoBackend: "", videoProviderI2V: "", videoProviderR2V: "",
    imageBackendDefault: "", imageBackendT2I: "", imageBackendI2I: "",
    audioOverride: null as boolean | null,
    narrationDelivery: "post_production",
    audioBackend: "", narrationVoice: "", narrationSpeed: null as number | null,
    voiceBinding: DEFAULT_CHARACTER_VOICE_BINDING,
    textDefault: "", textSimple: "", textComplex: "",
    aspectRatio: "", gridStoryboard: false,
    defaultDuration: null as number | null,
    speechRate: null as number | null,
    episodeTargetDuration: null as number | null,
    adTargetDuration: null as number | null,
    videoResolutions: {},
    imageResolution: null as string | null,
  });
  // 风格区独立保存，但"未保存就离开"也需被 isDirty 拦截。
  const initialStyleRef = useRef<StylePickerValue | null>(null);

  // 候选是全局配置、与项目无关，故不跟随下面按 projectName 重取的效果；reload 的标识随语言
  // 变化，语言切换时只刷新候选与译名，不重取项目表单。拉取失败也只影响细分区。
  useEffect(() => {
    void reloadCandidates();
  }, [reloadCandidates]);

  useEffect(() => {
    let disposed = false;
    voidCall(
      API.getAgentProfileStatus(projectName)
        .then((status) => {
          if (!disposed) setLoadedAgentProfile({ projectName, status });
        })
        .catch(() => {
          if (!disposed) setLoadedAgentProfile(null);
        }),
    );
    return () => {
      disposed = true;
    };
  }, [projectName]);

  useEffect(() => {
    let disposed = false;

    voidCall(Promise.all([
      API.getSystemConfig(),
      API.getProject(projectName),
      getProviderModels().catch(() => [] as ProviderInfo[]),
      getCustomProviderModels().catch(() => [] as CustomProviderInfo[]),
      API.getNarrationDefaults().catch(() => null),
    ]).then(([configRes, projectRes, providerList, customProviderList, ttsDefaults]) => {
      if (disposed) return;
      setNarrationDefaults(ttsDefaults);

      setOptions({
        video_backends: configRes.options?.video_backends ?? [],
        image_backends: configRes.options?.image_backends ?? [],
        text_backends: configRes.options?.text_backends ?? [],
        audio_backends: configRes.options?.audio_backends ?? [],
        provider_names: configRes.options?.provider_names,
        model_names: configRes.options?.model_names,
      });
      // 各层原样带入，不在此折叠回退——穿透演算由 ModelConfigSection 按解析链推导。
      const nextGlobals = {
        video: configRes.settings?.default_video_backend ?? "",
        videoI2V: configRes.settings?.default_video_backend_i2v ?? "",
        videoR2V: configRes.settings?.default_video_backend_r2v ?? "",
        image: configRes.settings?.default_image_backend ?? "",
        imageT2I: configRes.settings?.default_image_backend_t2i ?? "",
        imageI2I: configRes.settings?.default_image_backend_i2i ?? "",
        textDefault: configRes.settings?.default_text_backend ?? "",
        textSimple: configRes.settings?.text_backend_simple ?? "",
        textComplex: configRes.settings?.text_backend_complex ?? "",
        audio: configRes.settings?.default_audio_backend ?? "",
      };
      setGlobalDefaults(nextGlobals);
      setGlobalGenerateAudio(configRes.settings?.video_generate_audio ?? true);
      setProviders(providerList);
      setCustomProviders(customProviderList);

      const project = projectRes.project as unknown as Record<string, unknown>;
      const vb = (project.video_backend as string | undefined) ?? "";
      const vpi2v = (project.video_provider_i2v as string | undefined) ?? "";
      const vpr2v = (project.video_provider_r2v as string | undefined) ?? "";
      const ibDefault = (project.default_image_backend as string | undefined) ?? "";
      const ibt2i = (project.image_provider_t2i as string | undefined) ?? "";
      const ibi2i = (project.image_provider_i2i as string | undefined) ?? "";
      const rawAudio = project.video_generate_audio;
      const ao = typeof rawAudio === "boolean" ? rawAudio : null;
      const delivery: NarrationDelivery = project.narration_delivery === "use_tts" ? "use_tts" : "post_production";
      const ab = (project.audio_backend as string | undefined) ?? "";
      const nv = (project.narration_voice as string | undefined) ?? "";
      const rawSpeed = project.narration_speed;
      const ns = typeof rawSpeed === "number" && Number.isFinite(rawSpeed) ? rawSpeed : null;
      const td = (project.default_text_backend as string | undefined) ?? "";
      const tsi = (project.text_backend_simple as string | undefined) ?? "";
      const tcx = (project.text_backend_complex as string | undefined) ?? "";

      const rawAr = typeof project.aspect_ratio === "string" ? project.aspect_ratio : "";
      // Backend's get_aspect_ratio() falls back to "9:16" when unset (generation_tasks.py).
      // Mirror that here so the UI reflects the actually-effective ratio.
      const ar = rawAr || "9:16";
      const route = normalizeRoute(project.generation_mode);
      const grid = project.grid_storyboard === true;
      const dd = project.default_duration != null ? (project.default_duration as number) : null;
      const rawEtd = project.episode_target_duration;
      const etd = typeof rawEtd === "number" && Number.isFinite(rawEtd) ? rawEtd : null;
      const rawAtd = project.target_duration;
      const atd = typeof rawAtd === "number" && Number.isInteger(rawAtd) && rawAtd > 0 ? rawAtd : null;
      const rawRate = project.speech_rate_units_per_second;
      const sr = typeof rawRate === "number" && Number.isFinite(rawRate) ? rawRate : null;
      const sl = typeof project.source_language === "string" ? project.source_language : null;
      const vbind: CharacterVoiceBinding =
        project.character_voice_binding === "reference_audio" ? "reference_audio" : DEFAULT_CHARACTER_VOICE_BINDING;

      setVideoBackend(vb);
      setVideoProviderI2V(vpi2v);
      setVideoProviderR2V(vpr2v);
      setImageBackendDefault(ibDefault);
      setImageBackendT2I(ibt2i);
      setImageBackendI2I(ibi2i);
      setAudioOverride(ao);
      setNarrationDelivery(delivery);
      setAudioBackend(ab);
      setNarrationVoice(nv);
      setNarrationSpeed(ns);
      setTextDefault(td);
      setTextSimple(tsi);
      setTextComplex(tcx);
      setAspectRatio(ar);
      setGenerationRoute(route);
      setGridStoryboard(grid);
      setDefaultDuration(dd);
      setSpeechRate(sr);
      setEpisodeTargetDuration(etd);
      setAdTargetDuration(atd);
      setAdTargetLoadCount((count) => count + 1);
      setSourceLanguage(sl);
      setVoiceBinding(vbind);
      setProjectTitle(typeof project.title === "string" ? project.title : "");
      setContentMode(typeof project.content_mode === "string" ? project.content_mode : "narration");

      // model_settings 的 key 用执行模型（细分项 ‖ 项目默认 ‖ 全局细分 ‖ 全局默认），与
      // handleSave 一字不差——后端 resolve_resolution 就是按执行模型查这张表，键位对不上
      // 用户选的分辨率会被静默忽略。读侧另有一条兼容回退：视频回落 legacy video_model_settings。
      const executingVb = executingVideoModel(
        { videoBackend: vb, videoProviderI2V: vpi2v, videoProviderR2V: vpr2v },
        nextGlobals,
        route === "reference_video",
      );
      const executingI2V = executingVideoModel(
        { videoBackend: vb, videoProviderI2V: vpi2v, videoProviderR2V: vpr2v }, nextGlobals, false,
      );
      const executingIb = executingImageModel({ imageBackendDefault: ibDefault, imageBackendT2I: ibt2i }, nextGlobals);
      const ms = (project.model_settings ?? {}) as Record<string, { resolution: string | null }>;
      const legacyVideo = (project.video_model_settings ?? {}) as Record<string, { resolution?: string | null }>;
      const resolutions: Record<string, string | null> = Object.fromEntries(
        Object.entries(ms).map(([model, settings]) => [model, settings?.resolution ?? null]),
      );
      for (const model of new Set([executingVb, executingI2V])) {
        if (!model) continue;
        const modelId = model.includes("/") ? model.split("/")[1] : model;
        resolutions[model] = resolutions[model] ?? legacyVideo[modelId]?.resolution ?? null;
      }
      const iRes: string | null = executingIb ? (ms[executingIb]?.resolution ?? null) : null;
      setVideoResolutions(resolutions);
      setImageResolution(iRes);
      setModelSettings(ms);

      const derivedStyle = deriveStyleValue(project, projectName);
      setStyleValue(derivedStyle);
      initialStyleRef.current = derivedStyle;
      initialRef.current = {
        videoBackend: vb, videoProviderI2V: vpi2v, videoProviderR2V: vpr2v,
        imageBackendDefault: ibDefault, imageBackendT2I: ibt2i, imageBackendI2I: ibi2i,
        audioOverride: ao,
        narrationDelivery: delivery,
        audioBackend: ab, narrationVoice: nv, narrationSpeed: ns,
        voiceBinding: vbind,
        textDefault: td, textSimple: tsi, textComplex: tcx,
        aspectRatio: ar, gridStoryboard: grid, defaultDuration: dd, speechRate: sr,
        episodeTargetDuration: etd,
        adTargetDuration: atd,
        videoResolutions: resolutions, imageResolution: iRes,
      };
    }));

    return () => { disposed = true; };
  }, [projectName, reloadKey]);

  // blob: URL 所有权集中：StylePicker 只通过 onChange 更换引用，
  // revoke 统一在此 effect 做（URL 变更或卸载时）。
  useEffect(() => {
    const url = styleValue?.uploadedPreview;
    if (!url?.startsWith("blob:")) return;
    return () => URL.revokeObjectURL(url);
  }, [styleValue?.uploadedPreview]);

  // initialRef / initialStyleRef 是加载时快照，用于 dirty-check。
  /* eslint-disable react-hooks/refs -- ref 只在 fetch 完成时写一次，render 期读取稳定；改用 state 会在 fetch effect 内 setState */
  const styleIsDirty = (() => {
    const init = initialStyleRef.current;
    if (!styleValue || !init) return false;
    if (styleValue.mode !== init.mode) return true;
    if (styleValue.mode === "template") return styleValue.templateId !== init.templateId;
    // custom 模式：新上传文件、或既有图被用户清空（preview 从 URL 变为 null）
    return styleValue.uploadedFile !== null || styleValue.uploadedPreview !== init.uploadedPreview;
  })();

  // "无风格"态：模版未选 + 未上传新文件 + 未保留旧预览
  const isStyleCleared = !!styleValue
    && styleValue.templateId === null
    && styleValue.uploadedFile === null
    && !styleValue.uploadedPreview;

  const isDirty =
    videoBackend !== initialRef.current.videoBackend ||
    videoProviderI2V !== initialRef.current.videoProviderI2V ||
    videoProviderR2V !== initialRef.current.videoProviderR2V ||
    imageBackendDefault !== initialRef.current.imageBackendDefault ||
    imageBackendT2I !== initialRef.current.imageBackendT2I ||
    imageBackendI2I !== initialRef.current.imageBackendI2I ||
    audioOverride !== initialRef.current.audioOverride ||
    narrationDelivery !== initialRef.current.narrationDelivery ||
    audioBackend !== initialRef.current.audioBackend ||
    narrationVoice !== initialRef.current.narrationVoice ||
    narrationSpeed !== initialRef.current.narrationSpeed ||
    voiceBinding !== initialRef.current.voiceBinding ||
    textDefault !== initialRef.current.textDefault ||
    textSimple !== initialRef.current.textSimple ||
    textComplex !== initialRef.current.textComplex ||
    aspectRatio !== initialRef.current.aspectRatio ||
    gridStoryboard !== initialRef.current.gridStoryboard ||
    defaultDuration !== initialRef.current.defaultDuration ||
    speechRate !== initialRef.current.speechRate ||
    episodeTargetDuration !== initialRef.current.episodeTargetDuration ||
    adTargetDuration !== initialRef.current.adTargetDuration ||
    JSON.stringify(videoResolutions) !== JSON.stringify(initialRef.current.videoResolutions) ||
    imageResolution !== initialRef.current.imageResolution ||
    styleIsDirty;
  /* eslint-enable react-hooks/refs */

  useWarnUnsaved(isDirty);

  const [pendingNavigation, setPendingNavigation] = useState<string | null>(null);

  const guardedNavigate = useCallback((path: string) => {
    if (isDirty) {
      setPendingNavigation(path);
      return;
    }
    navigate(path);
  }, [isDirty, navigate]);

  const confirmDiscardAndNavigate = useCallback(() => {
    if (!pendingNavigation) return;
    const target = pendingNavigation;
    setPendingNavigation(null);
    navigate(target);
  }, [pendingNavigation, navigate]);

  // Cross-tab switch from custom → template may leave {mode:"template", templateId:null}
  // while an uploaded preview still lingers — no user-chosen card. Block save so
  // clicking it can't silently route to the "clear style" PATCH branch. The
  // explicit 取消风格 action zeroes uploadedFile/uploadedPreview too, bypassing this.
  const isStyleIncomplete =
    !!styleValue
    && styleValue.mode === "template"
    && !styleValue.templateId
    && (styleValue.uploadedFile !== null || !!styleValue.uploadedPreview);

  // PROTOTYPE #2971：风格并入统一保存栏（「保存方式」结论第 1 条），这里只是 handleSave 里的一步
  const saveStyle = useCallback(async () => {
    if (!styleValue) return;
    if (styleValue.mode === "template" && styleValue.templateId) {
      await API.updateProject(projectName, { style_template_id: styleValue.templateId });
    } else if (styleValue.mode === "custom" && styleValue.uploadedFile) {
      await API.uploadStyleImage(projectName, styleValue.uploadedFile);
    } else {
      // 取消风格：显式清掉模板 ID 与自定义图
      await API.updateProject(projectName, {
        style_template_id: null,
        clear_style_image: true,
      });
    }
    const refreshed = await API.getProject(projectName);
    const nextStyle = deriveStyleValue(refreshed.project as unknown as Record<string, unknown>, projectName);
    setStyleValue(nextStyle);
    initialStyleRef.current = nextStyle;
  }, [styleValue, projectName]);

  const handleClearStyle = useCallback(() => {
    if (!styleValue) return;
    setStyleValue({
      ...styleValue,
      templateId: null,
      uploadedFile: null,
      uploadedPreview: null,
    });
  }, [styleValue]);

  // 宫格是分镜图生视频内的装配选项；参考生视频与不支持宫格的 ad 项目下既不呈现也不参与保存
  const gridToggleVisible = generationRoute === "storyboard" && contentMode !== "ad";

  const narration: NarrationDeliveryValue = {
    delivery: narrationDelivery,
    audioBackend,
    narrationVoice,
    narrationSpeed,
  };

  const handleNarrationChange = useCallback(
    (next: NarrationDeliveryValue) => {
      let value = next;
      // 没有完整快照的项目切到 TTS 配音：缺的模型与音色、以及未设的语速按全局默认预填
      const hasSnapshot = next.audioBackend.includes("/") && next.narrationVoice.trim() !== "";
      if (next.delivery === "use_tts" && narrationDelivery !== "use_tts" && !hasSnapshot && narrationDefaults) {
        value = {
          ...next,
          audioBackend: next.audioBackend.includes("/") ? next.audioBackend : (narrationDefaults.audio_backend ?? ""),
          narrationVoice: next.narrationVoice.trim() ? next.narrationVoice : narrationDefaults.narration_voice,
          narrationSpeed: next.narrationSpeed ?? narrationDefaults.narration_speed,
        };
      }
      setNarrationDelivery(value.delivery);
      setAudioBackend(value.audioBackend);
      setNarrationVoice(value.narrationVoice);
      setNarrationSpeed(value.narrationSpeed);
    },
    [narrationDelivery, narrationDefaults],
  );

  const handleSave = useCallback(async (): Promise<boolean> => {
    const narrationProblem = narrationDeliveryProblem({
      delivery: narrationDelivery,
      audioBackend,
      narrationVoice,
      narrationSpeed,
    });
    if (narrationProblem) {
      useAppStore.getState().pushToast(
        t(narrationProblem === "model" ? "project_tts_model_required" : "project_narration_voice_required"),
        "error",
      );
      return false;
    }
    setSaving(true);
    try {
      // resolution 的 key 用执行模型（细分项 ‖ 项目默认 ‖ 全局细分 ‖ 全局默认），与读侧一致；
      // 后端按执行模型查这张表，键位对不上分辨率会被静默忽略。
      // 音色与后端 .strip() 对齐：保存时去首尾空白，避免本地基线带空格而磁盘值不带导致 isDirty 误报
      const trimmedVoice = narrationVoice.trim();
      // 旁白配置只写改动过的字段：旧项目可能留着裸供应商的音频后端，原样回写会被快照校验拒绝
      const initial = initialRef.current;
      const narrationPatch = {
        ...(narrationDelivery !== initial.narrationDelivery ? { narration_delivery: narrationDelivery } : {}),
        ...(audioBackend !== initial.audioBackend ? { audio_backend: audioBackend || null } : {}),
        ...(trimmedVoice !== initial.narrationVoice ? { narration_voice: trimmedVoice || null } : {}),
        ...(narrationSpeed !== initial.narrationSpeed ? { narration_speed: narrationSpeed } : {}),
      };
      const executingImage = executingImageModel({ imageBackendDefault, imageBackendT2I }, globalDefaults);
      const newModelSettings: Record<string, { resolution: string | null }> = { ...modelSettings };
      for (const [model, resolution] of Object.entries(videoResolutions)) {
        newModelSettings[model] = { resolution };
      }
      if (executingImage) {
        newModelSettings[executingImage] = { resolution: imageResolution };
      }

      await API.updateProject(projectName, {
        video_backend: videoBackend || null,
        video_provider_i2v: videoProviderI2V || null,
        video_provider_r2v: videoProviderR2V || null,
        default_image_backend: imageBackendDefault || null,
        image_provider_t2i: imageBackendT2I || null,
        image_provider_i2i: imageBackendI2I || null,
        video_generate_audio: audioOverride,
        ...narrationPatch,
        // 绑定方式只在参考生视频路线上有效，其余路线该键与项目无关，不写
        ...(generationRoute === "reference_video" ? { character_voice_binding: voiceBinding } : {}),
        // null 即清除项目级覆盖、回退语言默认
        speech_rate_units_per_second: speechRate,
        default_text_backend: textDefault || null,
        text_backend_simple: textSimple || null,
        text_backend_complex: textComplex || null,
        aspect_ratio: aspectRatio || undefined,
        // 生成模式不在 PATCH 面上（创建后不可更改）；宫格装配开关随时可写，
        // 但只在开关可见时写——参考生视频与 ad 项目下该键与项目无关，ad 更会对 true 返回 400
        ...(gridToggleVisible ? { grid_storyboard: gridStoryboard } : {}),
        // ad 项目禁写 default_duration（后端对字段出现本身返回 400），省略该键
        // ad 项目禁写 episode_target_duration（同 default_duration，字段出现即 400），省略该键；
        // 非 ad 恒写：null 即清除该偏好
        // ad 项目改写目标总时长（正整数秒，不可清空），非 ad 项目对该字段出现本身返回 400
        ...(contentMode === "ad"
          ? adTargetDuration !== null
            ? { target_duration: adTargetDuration }
            : {}
          : { default_duration: defaultDuration, episode_target_duration: episodeTargetDuration }),
        model_settings: newModelSettings,
      });
      setModelSettings(newModelSettings);
      setNarrationVoice(trimmedVoice);
      initialRef.current = {
        videoBackend, videoProviderI2V, videoProviderR2V,
        imageBackendDefault, imageBackendT2I, imageBackendI2I, audioOverride,
        narrationDelivery, audioBackend, narrationVoice: trimmedVoice, narrationSpeed,
        voiceBinding,
        textDefault, textSimple, textComplex,
        aspectRatio, gridStoryboard, defaultDuration, speechRate,
        episodeTargetDuration,
        adTargetDuration,
        videoResolutions, imageResolution,
      };
      // grid_storyboard / video_backend 落盘后，/video-capabilities 按已存值解析——查询 key 未变
      // 不会自动重取，需显式失效（同 MediaModelSection 保存流程）。
      useCapabilitiesStore.getState().invalidate();
      if (styleIsDirty) await saveStyle();
      // 「保存方式」第 6 条：成功不弹 toast，保存栏短暂显示「已保存」
      setJustSaved(true);
      window.setTimeout(() => setJustSaved(false), 2000);
      return true;
    } catch (e: unknown) {
      useAppStore.getState().pushToast(t("save_failed", { message: errMsg(e) }), "error");
      return false;
    } finally {
      setSaving(false);
    }
  }, [styleIsDirty, saveStyle, modelSettings, videoBackend, videoProviderI2V, videoProviderR2V, imageBackendDefault, imageBackendT2I, imageBackendI2I, audioOverride, narrationDelivery, audioBackend, narrationVoice, narrationSpeed, voiceBinding, textDefault, textSimple, textComplex, aspectRatio, generationRoute, gridStoryboard, gridToggleVisible, defaultDuration, speechRate, episodeTargetDuration, adTargetDuration, contentMode, videoResolutions, imageResolution, projectName, t, globalDefaults]);

  const handleResetAgentProfile = useCallback(async () => {
    if (profileResetProject !== projectName) {
      setProfileResetProject(null);
      return;
    }
    const resetProject = profileResetProject;
    setProfileResetting(true);
    try {
      const currentStatus = await API.getAgentProfileStatus(resetProject);
      const displayedStatus = loadedAgentProfile?.projectName === resetProject ? loadedAgentProfile.status : null;
      if (!displayedStatus || !sameProfileFiles(displayedStatus.customized_files, currentStatus.customized_files)) {
        setLoadedAgentProfile({ projectName: resetProject, status: currentStatus });
        if (!currentStatus.customized) setProfileResetProject(null);
        return;
      }
      const status = await API.resetAgentProfile(resetProject);
      setLoadedAgentProfile((current) => (
        current?.projectName === resetProject ? { projectName: resetProject, status } : current
      ));
      setProfileResetProject(null);
      useAppStore.getState().pushToast(t("agent_profile_reset_success"), "success");
    } catch (error: unknown) {
      useAppStore.getState().pushToast(t("agent_profile_reset_failed", { message: errMsg(error) }), "error");
    } finally {
      setProfileResetting(false);
    }
  }, [loadedAgentProfile, profileResetProject, projectName, t]);

  const handleOpenAgentProfileReset = useCallback(async () => {
    const resetProject = projectName;
    try {
      const status = await API.getAgentProfileStatus(resetProject);
      setLoadedAgentProfile({ projectName: resetProject, status });
      setProfileResetProject(status.customized ? resetProject : null);
    } catch (error: unknown) {
      useAppStore.getState().pushToast(t("agent_profile_reset_failed", { message: errMsg(error) }), "error");
    }
  }, [projectName, t]);

  // ─── PROTOTYPE #2971：表单拆成「组 → 分区」，交给变体决定摆法 ───────────────
  const { variant } = usePsParams();
  const discard = useCallback(() => setReloadKey((k) => k + 1), []);

  /* eslint-disable react-hooks/refs -- 与 isDirty 同理：加载快照只在 fetch 完成时写 */
  const init = initialRef.current;
  const basicsDirty =
    aspectRatio !== init.aspectRatio || gridStoryboard !== init.gridStoryboard || speechRate !== init.speechRate ||
    episodeTargetDuration !== init.episodeTargetDuration || adTargetDuration !== init.adTargetDuration;
  const modelsDirty =
    videoBackend !== init.videoBackend || videoProviderI2V !== init.videoProviderI2V || videoProviderR2V !== init.videoProviderR2V ||
    imageBackendDefault !== init.imageBackendDefault || imageBackendT2I !== init.imageBackendT2I || imageBackendI2I !== init.imageBackendI2I ||
    textDefault !== init.textDefault || textSimple !== init.textSimple || textComplex !== init.textComplex ||
    audioOverride !== init.audioOverride || defaultDuration !== init.defaultDuration ||
    JSON.stringify(videoResolutions) !== JSON.stringify(init.videoResolutions) || imageResolution !== init.imageResolution;
  const voiceDirty =
    narrationDelivery !== init.narrationDelivery || audioBackend !== init.audioBackend || narrationVoice !== init.narrationVoice ||
    narrationSpeed !== init.narrationSpeed || voiceBinding !== init.voiceBinding;
  /* eslint-enable react-hooks/refs */

  const overrideCount =
    [videoBackend, videoProviderI2V, videoProviderR2V, imageBackendDefault, imageBackendT2I, imageBackendI2I, textDefault, textSimple, textComplex]
      .filter(Boolean).length + (audioOverride !== null ? 1 : 0);
  const resetOverrides = () => {
    setVideoBackend(""); setVideoProviderI2V(""); setVideoProviderR2V("");
    setImageBackendDefault(""); setImageBackendT2I(""); setImageBackendI2I("");
    setTextDefault(""); setTextSimple(""); setTextComplex("");
    setAudioOverride(null);
  };

  const usesRef = generationRoute === "reference_video";
  const modelLabel = (m: string | undefined) => {
    if (!m) return "自动选择";
    const [prov, ...rest] = m.split("/");
    const model = rest.join("/");
    return `${allProviderNames[prov] ?? prov} · ${allModelNames[m] ?? allModelNames[model] ?? model}`;
  };
  const styleLabel = !styleValue
    ? "—"
    : styleValue.mode === "template" && styleValue.templateId
      ? t(`templates:name.${styleValue.templateId}`)
      : styleValue.mode === "custom" && styleValue.uploadedPreview
        ? "自定义参考图"
        : "未设置";
  const summary: PsSummaryRow[] = [
    { label: t("aspect_ratio_label"), value: aspectRatio === "16:9" ? t("landscape_16_9") : t("portrait_9_16"), source: "project", groupId: "basics" },
    { label: t("generation_route"), value: t(ROUTE_META[generationRoute].nameKey), source: "locked", groupId: "basics" },
    { label: "风格", value: styleLabel, source: "project", groupId: "style" },
    {
      label: "视频模型",
      value: modelLabel(executingVideoModel({ videoBackend, videoProviderI2V, videoProviderR2V }, globalDefaults, usesRef)),
      source: (usesRef ? videoProviderR2V : videoProviderI2V) || videoBackend ? "project" : "global",
      groupId: "models",
    },
    {
      label: "图片模型",
      value: modelLabel(executingImageModel({ imageBackendDefault, imageBackendT2I }, globalDefaults)),
      source: imageBackendT2I || imageBackendDefault ? "project" : "global",
      groupId: "models",
    },
    {
      label: "文本模型",
      value: modelLabel(effectiveModel(textDefault, globalDefaults.textDefault)),
      source: textDefault ? "project" : "global",
      groupId: "models",
    },
    {
      label: "生成有声视频",
      value: (audioOverride ?? globalGenerateAudio) ? "开启" : "关闭",
      source: audioOverride !== null ? "project" : "global",
      groupId: "models",
    },
    { label: "旁白", value: narrationDelivery === "use_tts" ? "TTS 配音" : "后期配音", source: "project", groupId: "voice" },
    {
      label: "口播语速估算",
      value: speechRate !== null ? `${speechRate} / 秒` : "按源文语言默认",
      source: speechRate !== null ? "project" : "global",
      groupId: "basics",
    },
  ];

  const aspectNode = (
    <fieldset>
      <legend className="sr-only">{t("aspect_ratio_label")}</legend>
      <div className="flex gap-2.5">
        {(["9:16", "16:9"] as const).map((ar) => (
          <label key={ar} className={radioCardClass(aspectRatio === ar)}>
            <input
              type="radio"
              name="aspectRatio"
              value={ar}
              checked={aspectRatio === ar}
              onChange={() => {
                setAspectRatio(ar);
                if (initialRef.current.aspectRatio && ar !== initialRef.current.aspectRatio) {
                  useAppStore.getState().pushToast(t("aspect_ratio_change_warning"), "warning");
                }
              }}
              className="sr-only"
            />
            <span className="inline-flex items-center gap-2">
              <span
                aria-hidden
                className="block rounded-[1.5px] border border-hairline"
                style={{
                  width: ar === "16:9" ? 12 : 7.5,
                  height: ar === "16:9" ? 7.5 : 12,
                  background: aspectRatio === ar ? "var(--color-primary-soft)" : "transparent",
                }}
              />
              {ar === "9:16" ? t("portrait_9_16") : t("landscape_16_9")}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );

  const routeNode = (
    <div className="space-y-2.5">
      <div className="rounded-lg border border-border bg-muted/30 px-3.5 py-2.5">
        <div className="flex items-center gap-2">
          <span className="text-[14px] text-text">{t(ROUTE_META[generationRoute].nameKey)}</span>
          <RouteLockBadge />
        </div>
        <p className="mt-0.5 text-[13px] leading-[1.5] text-text-3">{t(ROUTE_META[generationRoute].descKey)}</p>
      </div>
      {gridToggleVisible ? <GridStoryboardBar checked={gridStoryboard} onToggle={setGridStoryboard} /> : null}
    </div>
  );

  const pacingNode = (
    <div className="space-y-4">
      <SpeechRateField value={speechRate} onChange={setSpeechRate} sourceLanguage={sourceLanguage} />
      {contentMode === "ad" ? (
        <AdTargetDurationField key={adTargetLoadCount} value={adTargetDuration} onChange={setAdTargetDuration} />
      ) : (
        <EpisodeTargetDurationField value={episodeTargetDuration} onChange={setEpisodeTargetDuration} />
      )}
    </div>
  );

  const modelsNode = options ? (
    <ModelConfigSection
      projectName={projectName}
      value={{
        videoBackend,
        videoProviderI2V,
        videoProviderR2V,
        imageBackendDefault,
        imageBackendT2I,
        imageBackendI2I,
        textBackendDefault: textDefault,
        textBackendSimple: textSimple,
        textBackendComplex: textComplex,
        defaultDuration,
        videoResolution: videoResolutions[executingVideoModel(
          { videoBackend, videoProviderI2V, videoProviderR2V }, globalDefaults, usesRef,
        )] ?? null,
        videoResolutions,
        imageResolution,
      }}
      onChange={(next) => {
        setVideoBackend(next.videoBackend);
        setVideoProviderI2V(next.videoProviderI2V);
        setVideoProviderR2V(next.videoProviderR2V);
        setImageBackendDefault(next.imageBackendDefault);
        setImageBackendT2I(next.imageBackendT2I);
        setImageBackendI2I(next.imageBackendI2I);
        setTextDefault(next.textBackendDefault);
        setTextSimple(next.textBackendSimple);
        setTextComplex(next.textBackendComplex);
        setDefaultDuration(next.defaultDuration);
        setVideoResolutions(next.videoResolutions ?? videoResolutions);
        setImageResolution(next.imageResolution);
      }}
      providers={providers}
      customProviders={customProviders}
      options={{
        videoBackends: options.video_backends,
        imageBackends: options.image_backends,
        textBackends: options.text_backends,
        providerNames: allProviderNames,
        modelNames: allModelNames,
      }}
      candidates={candidates}
      candidatesError={candidatesError ? { onRetry: () => void reloadCandidates(), retrying: candidatesRetrying } : undefined}
      globalDefaults={globalDefaults}
      videoGenerateAudio={audioOverride}
      globalVideoGenerateAudio={globalGenerateAudio}
      onVideoGenerateAudioChange={setAudioOverride}
      usesReferenceImages={usesRef}
      enable={contentMode === "ad" ? { duration: false } : undefined}
    />
  ) : (
    <div className="flex items-center gap-2 py-6 text-[13px] text-text-3">
      <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden />
      {t("loading_config")}
    </div>
  );

  const voiceBindingNode = (
    <fieldset>
      <legend className="sr-only">{t("character_voice_binding_title")}</legend>
      <div className="flex gap-2.5">
        {(["prompt", "reference_audio"] as const).map((mode) => (
          <label key={mode} className={radioCardClass(voiceBinding === mode)}>
            <input
              type="radio"
              name="characterVoiceBinding"
              value={mode}
              checked={voiceBinding === mode}
              onChange={() => setVoiceBinding(mode)}
              className="sr-only"
            />
            <span>
              {mode === "prompt" ? t("character_voice_binding_prompt_label") : t("character_voice_binding_reference_audio_label")}
            </span>
          </label>
        ))}
      </div>
      <p className="mt-2 text-[13px] leading-relaxed text-text-3">
        {voiceBinding === "prompt" ? t("character_voice_binding_prompt_desc") : t("character_voice_binding_reference_audio_desc")}
      </p>
    </fieldset>
  );

  const profileNode = agentProfile ? (
    agentProfile.customized ? (
      <div className="space-y-3">
        <p className="text-[13px] text-warm">{t("agent_profile_customized")}</p>
        <ul className="space-y-1" aria-label={t("agent_profile_affected_files")}>
          {agentProfile.customized_files.map((file) => (
            <li key={file} className="font-mono text-[12px] text-text-3">{file}</li>
          ))}
        </ul>
        <Button variant="destructive" onClick={() => voidCall(handleOpenAgentProfileReset())}>
          {t("agent_profile_reset")}
        </Button>
      </div>
    ) : (
      <p className="text-[13px] text-text-3">{t("agent_profile_builtin")}</p>
    )
  ) : (
    <p className="text-[13px] text-text-3">{t("loading_config")}</p>
  );

  const groups: PsGroup[] = [
    {
      id: "basics",
      label: "基础",
      dirty: basicsDirty,
      items: [
        { id: "aspect", title: t("aspect_ratio_label"), node: aspectNode },
        { id: "route", title: t("generation_route"), node: routeNode },
        { id: "pacing", title: "时长与语速估算", node: pacingNode },
      ],
    },
    {
      id: "style",
      label: "风格",
      dirty: styleIsDirty,
      items: [
        {
          id: "style",
          title: t("project_style_section_title"),
          description: "生成分镜图和资产图时附加的画面风格。",
          node: styleValue ? (
            <StyleField
              value={styleValue}
              onChange={setStyleValue}
              mode={variant === "B" ? "inline" : "dialog"}
              canClear={!isStyleCleared}
              onClear={handleClearStyle}
            />
          ) : null,
        },
      ],
    },
    {
      id: "models",
      label: "模型",
      dirty: modelsDirty,
      // B 用摘要栏表达来源，不在侧栏和页头放覆盖计数
      overrides: variant === "B" ? undefined : overrideCount,
      items: [{ id: "models", title: t("model_config"), description: t("model_config_project_desc"), node: modelsNode }],
    },
    {
      id: "voice",
      label: "配音",
      dirty: voiceDirty,
      items: [
        { id: "narration", title: t("project_narration_delivery_title"), node: (
          <NarrationDeliveryFields
            value={narration}
            onChange={handleNarrationChange}
            audioBackends={options?.audio_backends ?? []}
            providerNames={allProviderNames}
            modelNames={allModelNames}
          />
        ) },
        ...(usesRef ? [{ id: "voice-binding", title: t("character_voice_binding_title"), node: voiceBindingNode }] : []),
      ],
    },
    {
      id: "memory",
      label: "项目记忆",
      agent: true,
      items: [{
        id: "memory",
        title: t("agent_memory_project_title"),
        description: t("agent_memory_project_desc"),
        node: <AgentMemoryCabinet scope={{ level: "project", projectName }} frame="card" />,
      }],
    },
    {
      id: "agent",
      label: "Agent 配置",
      agent: true,
      items: [{ id: "agent-profile", title: t("agent_profile_title"), description: t("agent_profile_description"), node: profileNode }],
    },
  ];

  const saveDisabled =
    saving ||
    !isDirty ||
    isStyleIncomplete ||
    !isValidSpeechRate(speechRate) ||
    !isValidEpisodeTargetDuration(episodeTargetDuration) ||
    (contentMode === "ad" && adTargetDuration === null);

  const saveBar = (
    <div className="flex w-full items-center justify-between gap-3">
      <span className="text-[13px] text-text-3" aria-live="polite">
        {saving ? t("common:saving") : justSaved ? t("saved") : isDirty ? t("unsaved_changes_hint") : ""}
      </span>
      <div className="flex shrink-0 items-center gap-2">
        <Button variant="ghost" disabled={!isDirty || saving} onClick={discard}>
          放弃修改
        </Button>
        <Button disabled={saveDisabled} onClick={() => void handleSave()}>
          {saving && <Loader2 data-icon="inline-start" className="motion-safe:animate-spin" />}
          {t("common:save")}
        </Button>
      </div>
    </div>
  );

  const dialogs = (
    <>
      <ConfirmDialog
        open={profileResetProject === projectName && agentProfile !== null}
        tone="danger"
        title={t("agent_profile_reset_confirm_title")}
        description={(
          <div className="space-y-2">
            <p>{t("agent_profile_reset_confirm_description")}</p>
            <ul className="space-y-1 font-mono text-[11px]">
              {agentProfile?.customized_files.map((file) => <li key={file}>{file}</li>)}
            </ul>
          </div>
        )}
        confirmLabel={t("agent_profile_reset_confirm")}
        loadingLabel={t("agent_profile_resetting")}
        loading={profileResetting}
        cancelLabel={t("common:cancel")}
        onCancel={() => setProfileResetProject(null)}
        onConfirm={handleResetAgentProfile}
      />
      <UnsavedGuardDialog
        open={pendingNavigation !== null}
        onStay={() => setPendingNavigation(null)}
        onDiscard={confirmDiscardAndNavigate}
        onSave={async () => {
          const ok = await handleSave();
          if (ok) confirmDiscardAndNavigate();
          return ok;
        }}
      />
    </>
  );

  const layoutProps: PsLayoutProps = {
    title: t("project_settings"),
    subtitle: getProjectDisplayName(projectTitle, t("untitled_project")),
    back: { label: t("back_to_project"), onClick: () => guardedNavigate(`/app/projects/${projectName}`) },
    groups,
    saveBar,
    isDirty,
    onSave: handleSave,
    onDiscard: discard,
    onResetOverrides: resetOverrides,
    summary,
    dialogs,
  };
  if (variant === "B") return <LayoutB {...layoutProps} />;
  if (variant === "C") return <LayoutC {...layoutProps} />;
  return <LayoutA {...layoutProps} />;
}
