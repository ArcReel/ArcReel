import { useParams, useLocation } from "wouter";
import { errMsg, voidCall, voidPromise } from "@/utils/async";
import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { ChevronLeft, Loader2 } from "lucide-react";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useCapabilitiesStore } from "@/stores/capabilities-store";
import { PROVIDER_NAMES } from "@/components/ui/ProviderIcon";
import { getProviderModels, getCustomProviderModels } from "@/utils/provider-models";
import { ModelConfigSection } from "@/components/shared/ModelConfigSection";
import { executingImageModel, executingVideoModel } from "@/components/shared/LayeredModelFields";
import { ProviderModelSelect } from "@/components/ui/ProviderModelSelect";
import { StylePicker, type StylePickerValue } from "@/components/shared/StylePicker";
import { DEFAULT_TEMPLATE_ID, STYLE_TEMPLATES } from "@/data/style-templates";
import type { CustomProviderInfo, ModelCandidatesResponse, ProviderInfo } from "@/types";
import { ROUTE_META, RouteLockBadge } from "@/components/shared/GenerationRouteCards";
import { GridStoryboardBar } from "@/components/shared/GridStoryboardBar";
import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE, GHOST_BTN_LG_CLS, radioCardClass } from "@/components/ui/darkroom-tokens";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useWarnUnsaved } from "@/hooks/useWarnUnsaved";
import { normalizeRoute, type GenerationRoute } from "@/utils/generation-mode";
import { getProjectDisplayName } from "@/utils/project-display";

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

// ─── Section card primitive ─────────────────────────────────────────────────

interface SectionCardProps {
  kicker: string;
  title?: string;
  description?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
}

function SectionCard({ kicker, title, description, children, footer }: SectionCardProps) {
  return (
    <section
      className="overflow-hidden rounded-[12px] border border-hairline"
      style={{
        background:
          "linear-gradient(180deg, oklch(0.20 0.012 270 / 0.55), oklch(0.16 0.010 265 / 0.55))",
        boxShadow:
          "inset 0 1px 0 oklch(1 0 0 / 0.03), 0 18px 40px -28px oklch(0 0 0 / 0.5)",
      }}
    >
      <header className="px-5 pt-4 pb-3 border-b border-hairline-soft">
        <div className="font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-accent-2">
          {kicker}
        </div>
        {title ? (
          <h2 className="mt-1 text-[15px] font-semibold tracking-tight text-text">{title}</h2>
        ) : null}
        {description ? (
          <p className="mt-1 text-[12px] leading-[1.55] text-text-3">{description}</p>
        ) : null}
      </header>
      <div className="px-5 py-4">{children}</div>
      {footer ? (
        <footer className="border-t border-hairline-soft bg-[oklch(0.16_0.010_265_/_0.5)] px-5 py-3">
          {footer}
        </footer>
      ) : null}
    </section>
  );
}

// ─── Component ───────────────────────────────────────────────────────────────

export function ProjectSettingsPage() {
  const { t } = useTranslation("dashboard");
  const params = useParams<{ projectName: string }>();
  const projectName = params.projectName || "";
  const [, navigate] = useLocation();

  const [options, setOptions] = useState<{
    video_backends: string[];
    image_backends: string[];
    text_backends: string[];
    audio_backends: string[];
    provider_names?: Record<string, string>;
  } | null>(null);
  const [candidates, setCandidates] = useState<ModelCandidatesResponse | null>(null);
  const [candidatesError, setCandidatesError] = useState(false);
  const candidatesAbortRef = useRef<AbortController | null>(null);
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

  const allProviderNames = useMemo(
    () => ({ ...PROVIDER_NAMES, ...(options?.provider_names ?? {}) }),
    [options],
  );

  // Project-level overrides (from project.json)
  // "" means "follow global default"
  const [videoBackend, setVideoBackend] = useState<string>("");
  const [videoProviderI2V, setVideoProviderI2V] = useState<string>("");
  const [videoProviderR2V, setVideoProviderR2V] = useState<string>("");
  const [imageBackendDefault, setImageBackendDefault] = useState<string>("");
  const [imageBackendT2I, setImageBackendT2I] = useState<string>("");
  const [imageBackendI2I, setImageBackendI2I] = useState<string>("");
  const [audioOverride, setAudioOverride] = useState<boolean | null>(null);
  // 旁白配音（TTS）项目级覆盖：空字符串/ null 表示跟随全局默认
  const [audioBackend, setAudioBackend] = useState<string>("");
  const [narrationVoice, setNarrationVoice] = useState<string>("");
  const [narrationSpeed, setNarrationSpeed] = useState<number | null>(null);
  const [textDefault, setTextDefault] = useState<string>("");
  const [textSimple, setTextSimple] = useState<string>("");
  const [textComplex, setTextComplex] = useState<string>("");
  const [aspectRatio, setAspectRatio] = useState<string>("");
  // 路线创建时锁定，此页只读展示；宫格装配开关随时可切
  const [generationRoute, setGenerationRoute] = useState<GenerationRoute>("storyboard");
  const [gridStoryboard, setGridStoryboard] = useState(false);
  const [defaultDuration, setDefaultDuration] = useState<number | null>(null);
  const [videoResolution, setVideoResolution] = useState<string | null>(null);
  const [imageResolution, setImageResolution] = useState<string | null>(null);
  const [modelSettings, setModelSettings] = useState<Record<string, { resolution: string | null }>>({});
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [customProviders, setCustomProviders] = useState<CustomProviderInfo[]>([]);
  const [projectTitle, setProjectTitle] = useState<string>("");
  const [contentMode, setContentMode] = useState<string>("narration");
  const [saving, setSaving] = useState(false);

  // ── Style picker state (independent save flow) ─────────────────────────────
  const [styleValue, setStyleValue] = useState<StylePickerValue | null>(null);
  const [savingStyle, setSavingStyle] = useState(false);
  const initialRef = useRef({
    videoBackend: "", videoProviderI2V: "", videoProviderR2V: "",
    imageBackendDefault: "", imageBackendT2I: "", imageBackendI2I: "",
    audioOverride: null as boolean | null,
    audioBackend: "", narrationVoice: "", narrationSpeed: null as number | null,
    textDefault: "", textSimple: "", textComplex: "",
    aspectRatio: "", gridStoryboard: false,
    defaultDuration: null as number | null,
    videoResolution: null as string | null,
    imageResolution: null as string | null,
  });
  // 风格区独立保存，但"未保存就离开"也需被 isDirty 拦截。
  const initialStyleRef = useRef<StylePickerValue | null>(null);

  // 候选是全局配置，与项目无关，独立于下面按 projectName 重取的效果；失败时不动其余表单状态，
  // 供挂载与重试共用。接管方轮换 controller——重试可能在上一轮请求尚未回来时触发。
  const loadCandidates = useCallback(async () => {
    candidatesAbortRef.current?.abort();
    const controller = new AbortController();
    candidatesAbortRef.current = controller;
    try {
      const cand = await API.getModelCandidates({ signal: controller.signal });
      if (controller.signal.aborted) return;
      setCandidates(cand);
      setCandidatesError(false);
    } catch {
      if (controller.signal.aborted) return;
      setCandidates(null);
      setCandidatesError(true);
    }
  }, []);

  useEffect(() => {
    // 挂载时异步拉取候选，回调内 setCandidates 等（异步 fetch 后回写）
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadCandidates();
    return () => candidatesAbortRef.current?.abort();
  }, [loadCandidates]);

  useEffect(() => {
    let disposed = false;

    voidCall(Promise.all([
      API.getSystemConfig(),
      API.getProject(projectName),
      getProviderModels().catch(() => [] as ProviderInfo[]),
      getCustomProviderModels().catch(() => [] as CustomProviderInfo[]),
    ]).then(([configRes, projectRes, providerList, customProviderList]) => {
      if (disposed) return;

      setOptions({
        video_backends: configRes.options?.video_backends ?? [],
        image_backends: configRes.options?.image_backends ?? [],
        text_backends: configRes.options?.text_backends ?? [],
        audio_backends: configRes.options?.audio_backends ?? [],
        provider_names: configRes.options?.provider_names,
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

      setVideoBackend(vb);
      setVideoProviderI2V(vpi2v);
      setVideoProviderR2V(vpr2v);
      setImageBackendDefault(ibDefault);
      setImageBackendT2I(ibt2i);
      setImageBackendI2I(ibi2i);
      setAudioOverride(ao);
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
      const executingIb = executingImageModel({ imageBackendDefault: ibDefault, imageBackendT2I: ibt2i }, nextGlobals);
      const ms = (project.model_settings ?? {}) as Record<string, { resolution: string | null }>;
      const legacyVideo = (project.video_model_settings ?? {}) as Record<string, { resolution?: string | null }>;
      const vModelId = executingVb && executingVb.includes("/") ? executingVb.split("/")[1] : executingVb;
      const vRes: string | null =
        (executingVb ? (ms[executingVb]?.resolution ?? null) : null) ||
        (vModelId ? (legacyVideo[vModelId]?.resolution ?? null) : null) ||
        null;
      const iRes: string | null = executingIb ? (ms[executingIb]?.resolution ?? null) : null;
      setVideoResolution(vRes);
      setImageResolution(iRes);
      setModelSettings(ms);

      const derivedStyle = deriveStyleValue(project, projectName);
      setStyleValue(derivedStyle);
      initialStyleRef.current = derivedStyle;
      initialRef.current = {
        videoBackend: vb, videoProviderI2V: vpi2v, videoProviderR2V: vpr2v,
        imageBackendDefault: ibDefault, imageBackendT2I: ibt2i, imageBackendI2I: ibi2i,
        audioOverride: ao,
        audioBackend: ab, narrationVoice: nv, narrationSpeed: ns,
        textDefault: td, textSimple: tsi, textComplex: tcx,
        aspectRatio: ar, gridStoryboard: grid, defaultDuration: dd,
        videoResolution: vRes, imageResolution: iRes,
      };
    }));

    return () => { disposed = true; };
  }, [projectName]);

  // blob: URL 所有权集中：StylePicker 只通过 onChange 更换引用，
  // revoke 统一在此 effect 做（URL 变更或卸载时）。
  useEffect(() => {
    const url = styleValue?.uploadedPreview;
    if (!url?.startsWith("blob:")) return;
    return () => URL.revokeObjectURL(url);
  }, [styleValue?.uploadedPreview]);

  // initialRef / initialStyleRef 是加载时快照，用于 dirty-check。
  // react-hooks v7 的 react-hooks/refs 规则禁止 render 阶段读 ref，
  // 但本场景 ref 内容只在 fetch 完成时写一次，render 阶段读是稳定的。
  // 改 state 会导致 fetch effect 内 setState 触发 set-state-in-effect。
  /* eslint-disable react-hooks/refs */
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
  const hasInitialStyle = !!initialStyleRef.current
    && (initialStyleRef.current.templateId !== null
      || initialStyleRef.current.uploadedPreview !== null);

  const isDirty =
    videoBackend !== initialRef.current.videoBackend ||
    videoProviderI2V !== initialRef.current.videoProviderI2V ||
    videoProviderR2V !== initialRef.current.videoProviderR2V ||
    imageBackendDefault !== initialRef.current.imageBackendDefault ||
    imageBackendT2I !== initialRef.current.imageBackendT2I ||
    imageBackendI2I !== initialRef.current.imageBackendI2I ||
    audioOverride !== initialRef.current.audioOverride ||
    audioBackend !== initialRef.current.audioBackend ||
    narrationVoice !== initialRef.current.narrationVoice ||
    narrationSpeed !== initialRef.current.narrationSpeed ||
    textDefault !== initialRef.current.textDefault ||
    textSimple !== initialRef.current.textSimple ||
    textComplex !== initialRef.current.textComplex ||
    aspectRatio !== initialRef.current.aspectRatio ||
    gridStoryboard !== initialRef.current.gridStoryboard ||
    defaultDuration !== initialRef.current.defaultDuration ||
    videoResolution !== initialRef.current.videoResolution ||
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
  const isStyleSaveDisabled = savingStyle || !styleIsDirty || isStyleIncomplete;

  const handleSaveStyle = useCallback(async () => {
    if (!styleValue) return;
    setSavingStyle(true);
    try {
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
      // Refetch project to reset styleValue from canonical server state
      const refreshed = await API.getProject(projectName);
      const nextStyle = deriveStyleValue(refreshed.project as unknown as Record<string, unknown>, projectName);
      setStyleValue(nextStyle);
      initialStyleRef.current = nextStyle;
      useAppStore.getState().pushToast(t("saved"), "success");
    } catch (e: unknown) {
      useAppStore.getState().pushToast(t("save_failed", { message: errMsg(e) }), "error");
    } finally {
      setSavingStyle(false);
    }
  }, [styleValue, projectName, t]);

  const handleClearStyle = useCallback(() => {
    if (!styleValue) return;
    setStyleValue({
      ...styleValue,
      templateId: null,
      uploadedFile: null,
      uploadedPreview: null,
    });
  }, [styleValue]);

  // 宫格是分镜路线内的装配选项；参考路线与不支持宫格的 ad 项目下既不呈现也不参与保存
  const gridToggleVisible = generationRoute === "storyboard" && contentMode !== "ad";

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      // resolution 的 key 用执行模型（细分项 ‖ 项目默认 ‖ 全局细分 ‖ 全局默认），与读侧一致；
      // 后端按执行模型查这张表，键位对不上分辨率会被静默忽略。
      // 音色与后端 .strip() 对齐：保存时去首尾空白，避免本地基线带空格而磁盘值不带导致 isDirty 误报
      const trimmedVoice = narrationVoice.trim();
      const executingVideo = executingVideoModel(
        { videoBackend, videoProviderI2V, videoProviderR2V },
        globalDefaults,
        generationRoute === "reference_video",
      );
      const executingImage = executingImageModel({ imageBackendDefault, imageBackendT2I }, globalDefaults);
      const newModelSettings: Record<string, { resolution: string | null }> = { ...modelSettings };
      if (executingVideo) {
        newModelSettings[executingVideo] = { resolution: videoResolution };
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
        audio_backend: audioBackend || null,
        narration_voice: trimmedVoice || null,
        narration_speed: narrationSpeed,
        default_text_backend: textDefault || null,
        text_backend_simple: textSimple || null,
        text_backend_complex: textComplex || null,
        aspect_ratio: aspectRatio || undefined,
        // 路线不在 PATCH 面上（创建后不可更改）；宫格装配开关随时可写，
        // 但只在开关可见时写——参考路线与 ad 项目下该键与项目无关，ad 更会对 true 返回 400
        ...(gridToggleVisible ? { grid_storyboard: gridStoryboard } : {}),
        // ad 项目禁写 default_duration（后端对字段出现本身返回 400），省略该键
        ...(contentMode === "ad" ? {} : { default_duration: defaultDuration }),
        model_settings: newModelSettings,
      });
      setModelSettings(newModelSettings);
      setNarrationVoice(trimmedVoice);
      initialRef.current = {
        videoBackend, videoProviderI2V, videoProviderR2V,
        imageBackendDefault, imageBackendT2I, imageBackendI2I, audioOverride,
        audioBackend, narrationVoice: trimmedVoice, narrationSpeed,
        textDefault, textSimple, textComplex,
        aspectRatio, gridStoryboard, defaultDuration,
        videoResolution, imageResolution,
      };
      // grid_storyboard / video_backend 落盘后，/video-capabilities 按已存值解析——查询 key 未变
      // 不会自动重取，需显式失效（同 MediaModelSection 保存流程）。
      useCapabilitiesStore.getState().invalidate();
      useAppStore.getState().pushToast(t("saved"), "success");
    } catch (e: unknown) {
      useAppStore.getState().pushToast(t("save_failed", { message: errMsg(e) }), "error");
    } finally {
      setSaving(false);
    }
  }, [modelSettings, videoBackend, videoProviderI2V, videoProviderR2V, imageBackendDefault, imageBackendT2I, imageBackendI2I, audioOverride, audioBackend, narrationVoice, narrationSpeed, textDefault, textSimple, textComplex, aspectRatio, generationRoute, gridStoryboard, gridToggleVisible, defaultDuration, contentMode, videoResolution, imageResolution, projectName, t, globalDefaults]);

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col text-text"
      style={
        {
          background:
            "radial-gradient(900px 480px at 8% -10%, oklch(0.32 0.05 295 / 0.22), transparent 55%), radial-gradient(800px 460px at 100% 110%, oklch(0.26 0.04 260 / 0.22), transparent 55%), linear-gradient(180deg, var(--color-bg-grad-a), var(--color-bg-grad-b))",
        }
      }
    >
      {/* ─── Sticky top bar ─── */}
      <header
        className="sticky top-0 z-30 shrink-0"
        style={{
          background:
            "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.55), oklch(0.15 0.010 265 / 0.45))",
          backdropFilter: "blur(28px) saturate(1.5)",
          WebkitBackdropFilter: "blur(28px) saturate(1.5)",
          borderBottom: "1px solid var(--color-hairline)",
          boxShadow:
            "inset 0 1px 0 oklch(1 0 0 / 0.05), 0 6px 24px -12px oklch(0 0 0 / 0.45)",
        }}
      >
        <div className="mx-auto flex max-w-3xl items-center gap-4 px-6 py-4">
          <button
            onClick={() => guardedNavigate(`/app/projects/${projectName}`)}
            className="inline-flex items-center gap-1.5 rounded-md border border-hairline-soft bg-bg-grad-a/45 px-2.5 py-1.5 text-[12px] text-text-3 transition-colors hover:border-hairline hover:bg-bg-grad-a hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            aria-label={t("back_to_project")}
          >
            <ChevronLeft className="h-3.5 w-3.5" />
            <span>{t("back_to_project")}</span>
          </button>
          <span aria-hidden className="h-5 w-px bg-hairline-soft" />
          <div className="min-w-0 flex-1">
            <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-accent-2">
              Project Booth — {projectName.toUpperCase()}
            </div>
            <h1
              className="font-editorial mt-0.5 truncate"
              style={{
                fontWeight: 400,
                fontSize: 24,
                lineHeight: 1.05,
                letterSpacing: "-0.012em",
                color: "var(--color-text)",
              }}
              title={getProjectDisplayName(projectTitle, t("untitled_project"))}
            >
              {t("project_settings")}
              <span className="ml-2 align-middle font-mono text-[11.5px] font-medium uppercase tracking-[0.08em] text-text-3">
                {getProjectDisplayName(projectTitle, t("untitled_project"))}
              </span>
            </h1>
          </div>
        </div>
      </header>

      {/* ─── Scrollable body ─── */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-6 py-7 pb-24 space-y-5">
          <div>
            <div className="font-mono text-[9.5px] font-bold uppercase tracking-[0.16em] text-text-3">
              {t("model_config")}
            </div>
            <p className="mt-1 text-[12.5px] leading-[1.55] text-text-3">
              {t("model_config_project_desc")}
            </p>
          </div>

          {/* Style picker (independent save flow, mutually exclusive template / custom) */}
          {styleValue && (
            <SectionCard
              kicker="Visual Style"
              title={t("project_style_section_title")}
              footer={
                <div className="flex items-center gap-3">
                  <button
                    type="button"
                    // handleSaveStyle 在 onClick 时才执行，ref 写入是合法的；规则误报。
                    // eslint-disable-next-line react-hooks/refs
                    onClick={voidPromise(handleSaveStyle)}
                    disabled={isStyleSaveDisabled}
                    className={ACCENT_BTN_CLS}
                    style={ACCENT_BUTTON_STYLE}
                  >
                    {savingStyle && (
                      <Loader2 aria-hidden className="h-3.5 w-3.5 motion-safe:animate-spin" />
                    )}
                    {savingStyle ? t("style_saving") : t("style_save")}
                  </button>
                  {hasInitialStyle && !isStyleCleared && !savingStyle && (
                    <button
                      type="button"
                      onClick={handleClearStyle}
                      className="rounded-[7px] px-2.5 py-1.5 text-[12px] text-text-3 transition-colors hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                    >
                      {t("style_clear")}
                    </button>
                  )}
                  {isStyleCleared && !savingStyle && styleIsDirty && (
                    <p className="text-[11.5px] text-text-3">{t("style_cleared_hint")}</p>
                  )}
                </div>
              }
            >
              <StylePicker value={styleValue} onChange={setStyleValue} />
            </SectionCard>
          )}

          {options && (
            <>
              {/* Model config (video + duration + image + text) */}
              <SectionCard kicker="Engine Routing" title={t("model_config")}>
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
                    videoResolution,
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
                    setVideoResolution(next.videoResolution);
                    setImageResolution(next.imageResolution);
                  }}
                  providers={providers}
                  customProviders={customProviders}
                  options={{
                    videoBackends: options.video_backends,
                    imageBackends: options.image_backends,
                    textBackends: options.text_backends,
                    providerNames: allProviderNames,
                  }}
                  candidates={candidates}
                  candidatesError={candidatesError}
                  onRetryCandidates={() => void loadCandidates()}
                  globalDefaults={{
                    video: globalDefaults.video,
                    videoI2V: globalDefaults.videoI2V,
                    videoR2V: globalDefaults.videoR2V,
                    image: globalDefaults.image,
                    imageT2I: globalDefaults.imageT2I,
                    imageI2I: globalDefaults.imageI2I,
                    textDefault: globalDefaults.textDefault,
                    textSimple: globalDefaults.textSimple,
                    textComplex: globalDefaults.textComplex,
                  }}
                  videoGenerateAudio={audioOverride}
                  onVideoGenerateAudioChange={setAudioOverride}
                  usesReferenceImages={generationRoute === "reference_video"}
                  enable={contentMode === "ad" ? { duration: false } : undefined}
                />
              </SectionCard>

              {/* Aspect ratio */}
              <SectionCard kicker="Frame Aspect">
                <fieldset>
                  <legend className="mb-2.5 block font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-3">
                    {t("aspect_ratio_label")}
                  </legend>
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
                              useAppStore.getState().pushToast(
                                t("aspect_ratio_change_warning"),
                                "warning",
                              );
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
                              background:
                                aspectRatio === ar ? "var(--color-accent-soft)" : "transparent",
                            }}
                          />
                          {ar === "9:16" ? t("portrait_9_16") : t("landscape_16_9")}
                        </span>
                      </label>
                    ))}
                  </div>
                </fieldset>
              </SectionCard>

              {/* Generation route — 创建时锁定，此处只读；宫格装配开关随时可切 */}
              <SectionCard kicker="Pipeline Mode">
                <div className="space-y-2.5">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-3">
                      {t("generation_route")}
                    </span>
                    <RouteLockBadge />
                  </div>
                  <div className="rounded-[9px] border border-hairline-soft bg-bg-grad-a/50 px-3.5 py-2.5">
                    <div className="flex items-baseline gap-2">
                      <span className="text-[13px] font-semibold text-text">
                        {t(ROUTE_META[generationRoute].nameKey)}
                      </span>
                      <span className="font-mono text-[9px] font-bold uppercase tracking-[0.14em] text-text-4">
                        {ROUTE_META[generationRoute].tag}
                      </span>
                    </div>
                    <p className="mt-0.5 text-[11.5px] leading-[1.5] text-text-3">
                      {t(ROUTE_META[generationRoute].descKey)}
                    </p>
                  </div>
                  {gridToggleVisible ? (
                    <GridStoryboardBar checked={gridStoryboard} onToggle={setGridStoryboard} />
                  ) : null}
                </div>
              </SectionCard>

              {/* 旁白配音（TTS）：仅 narration 模式消费——TTS 绑定 segment.novel_text，drama/ad 无该字段，
                  故与两个画布的批量旁白按钮（contentMode === "narration"）同口径门控，避免对无效模式展示配音卡 */}
              {contentMode === "narration" && (
              <SectionCard kicker="Audio Channel" title={t("media_narration_title")}>
                <div className="space-y-4">
                  <div>
                    <div className="mb-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4">
                      {t("default_audio_model")}
                    </div>
                    <ProviderModelSelect
                      value={audioBackend}
                      options={options.audio_backends}
                      providerNames={allProviderNames}
                      onChange={setAudioBackend}
                      allowDefault
                      defaultLabel={t("follow_global_default")}
                      fallbackValue={globalDefaults.audio || undefined}
                      aria-label={t("default_audio_model")}
                    />
                  </div>
                  <div>
                    <label
                      htmlFor="project-narration-voice"
                      className="mb-1.5 block font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4"
                    >
                      {t("narration_voice_label")}
                    </label>
                    <input
                      id="project-narration-voice"
                      type="text"
                      value={narrationVoice}
                      onChange={(e) => setNarrationVoice(e.target.value)}
                      className="w-full rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-2 text-[12.5px] text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                    />
                    <p className="mt-1 text-[11px] text-text-4">{t("narration_voice_hint")}</p>
                  </div>
                  <div>
                    <label
                      htmlFor="project-narration-speed"
                      className="mb-1.5 block font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4"
                    >
                      {t("narration_speed_label")}
                    </label>
                    <input
                      id="project-narration-speed"
                      type="number"
                      min={0.1}
                      step={0.1}
                      value={narrationSpeed ?? ""}
                      onChange={(e) => {
                        const raw = e.target.value;
                        if (raw === "") {
                          setNarrationSpeed(null);
                          return;
                        }
                        const next = Number(raw);
                        // 仅过滤非有限数：NaN/Infinity 会被序列化为 null 误触"清除"语义；
                        // 正数约束交由保存时后端校验兜底
                        if (Number.isFinite(next)) setNarrationSpeed(next);
                      }}
                      className="w-full rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-2 text-[12.5px] text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                    />
                    <p className="mt-1 text-[11px] text-text-4">{t("narration_speed_hint")}</p>
                  </div>
                </div>
              </SectionCard>
              )}
            </>
          )}

          {!options && (
            <div className="flex items-center gap-2 py-6 text-text-3">
              <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin text-accent-2" aria-hidden />
              <span className="font-mono text-[11px] uppercase tracking-[0.14em]">
                {t("loading_config")}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* ─── Sticky save bar ─── */}
      <footer
        className="shrink-0"
        style={{
          background:
            "linear-gradient(180deg, oklch(0.18 0.011 265 / 0.65), oklch(0.14 0.009 265 / 0.85))",
          backdropFilter: "blur(20px) saturate(1.3)",
          WebkitBackdropFilter: "blur(20px) saturate(1.3)",
          borderTop: "1px solid var(--color-hairline)",
          boxShadow: "0 -8px 28px -12px oklch(0 0 0 / 0.55)",
        }}
      >
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-3 px-6 py-3">
          <div className="min-w-0 flex items-center gap-2 text-[11.5px] text-text-3">
            <span
              aria-hidden
              className="inline-block h-1.5 w-1.5 rounded-full"
              style={{
                background: isDirty ? "var(--color-warm)" : "var(--color-good)",
                boxShadow: isDirty
                  ? "0 0 6px oklch(0.85 0.13 75 / 0.4)"
                  : "0 0 6px oklch(0.78 0.10 155 / 0.4)",
              }}
            />
            <span className="font-mono text-[10px] font-bold uppercase tracking-[0.14em]">
              {isDirty ? t("unsaved_changes_hint") : t("saved")}
            </span>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button
              onClick={() => guardedNavigate(`/app/projects/${projectName}`)}
              className={GHOST_BTN_LG_CLS}
            >
              {t("common:cancel")}
            </button>
            <button
              // handleSave 在 onClick 时才执行；规则误报。
              // eslint-disable-next-line react-hooks/refs
              onClick={voidPromise(handleSave)}
              disabled={saving}
              className={`${ACCENT_BTN_CLS} px-5`}
              style={ACCENT_BUTTON_STYLE}
            >
              {saving && <Loader2 aria-hidden className="h-3.5 w-3.5 motion-safe:animate-spin" />}
              {saving ? t("common:saving") : t("common:save")}
            </button>
          </div>
        </div>
      </footer>

      <ConfirmDialog
        open={pendingNavigation !== null}
        tone="danger"
        title={t("unsaved_changes_confirm")}
        confirmLabel={t("common:confirm")}
        cancelLabel={t("common:cancel")}
        onCancel={() => setPendingNavigation(null)}
        onConfirm={confirmDiscardAndNavigate}
      />
    </div>
  );
}
