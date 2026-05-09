import { useParams, useLocation } from "wouter";
import { errMsg, voidCall, voidPromise } from "@/utils/async";
import { useState, useEffect, useCallback, useRef, useMemo, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { ChevronLeft, Loader2 } from "lucide-react";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { PROVIDER_NAMES } from "@/components/ui/ProviderIcon";
import { getProviderModels, getCustomProviderModels } from "@/utils/provider-models";
import { ModelConfigSection } from "@/components/shared/ModelConfigSection";
import { StylePicker, type StylePickerValue } from "@/components/shared/StylePicker";
import { DEFAULT_TEMPLATE_ID, STYLE_TEMPLATES } from "@/data/style-templates";
import type { CustomProviderInfo, ProviderInfo } from "@/types";
import { GenerationModeSelector } from "@/components/shared/GenerationModeSelector";
import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE, GHOST_BTN_LG_CLS, radioCardClass } from "@/components/ui/darkroom-tokens";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { normalizeMode, type GenerationMode } from "@/utils/generation-mode";

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
    provider_names?: Record<string, string>;
  } | null>(null);
  const [globalDefaults, setGlobalDefaults] = useState<{
    video: string;
    imageT2I: string;
    imageI2I: string;
    textScript: string;
    textOverview: string;
    textStyle: string;
  }>({ video: "", imageT2I: "", imageI2I: "", textScript: "", textOverview: "", textStyle: "" });

  const allProviderNames = useMemo(
    () => ({ ...PROVIDER_NAMES, ...(options?.provider_names ?? {}) }),
    [options],
  );

  // Project-level overrides (from project.json)
  // "" means "follow global default"
  const [videoBackend, setVideoBackend] = useState<string>("");
  const [imageBackendT2I, setImageBackendT2I] = useState<string>("");
  const [imageBackendI2I, setImageBackendI2I] = useState<string>("");
  const [audioOverride, setAudioOverride] = useState<boolean | null>(null);
  const [textScript, setTextScript] = useState<string>("");
  const [textOverview, setTextOverview] = useState<string>("");
  const [textStyle, setTextStyle] = useState<string>("");
  const [aspectRatio, setAspectRatio] = useState<string>("");
  const [generationMode, setGenerationMode] = useState<GenerationMode>("storyboard");
  const [defaultDuration, setDefaultDuration] = useState<number | null>(null);
  const [videoResolution, setVideoResolution] = useState<string | null>(null);
  const [imageResolution, setImageResolution] = useState<string | null>(null);
  const [modelSettings, setModelSettings] = useState<Record<string, { resolution: string | null }>>({});
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [customProviders, setCustomProviders] = useState<CustomProviderInfo[]>([]);
  const [projectTitle, setProjectTitle] = useState<string>("");
  const [saving, setSaving] = useState(false);

  // ── Style picker state (independent save flow) ─────────────────────────────
  const [styleValue, setStyleValue] = useState<StylePickerValue | null>(null);
  const [savingStyle, setSavingStyle] = useState(false);
  const initialRef = useRef({
    videoBackend: "", imageBackendT2I: "", imageBackendI2I: "", audioOverride: null as boolean | null,
    textScript: "", textOverview: "", textStyle: "",
    aspectRatio: "", generationMode: "storyboard" as GenerationMode,
    defaultDuration: null as number | null,
    videoResolution: null as string | null,
    imageResolution: null as string | null,
  });
  // 风格区独立保存，但"未保存就离开"也需被 isDirty 拦截。
  const initialStyleRef = useRef<StylePickerValue | null>(null);

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
        provider_names: configRes.options?.provider_names,
      });
      setGlobalDefaults({
        video: configRes.settings?.default_video_backend ?? "",
        imageT2I:
          configRes.settings?.default_image_backend_t2i ??
          configRes.settings?.default_image_backend ??
          "",
        imageI2I:
          configRes.settings?.default_image_backend_i2i ??
          configRes.settings?.default_image_backend ??
          "",
        textScript: configRes.settings?.text_backend_script ?? "",
        textOverview: configRes.settings?.text_backend_overview ?? "",
        textStyle: configRes.settings?.text_backend_style ?? "",
      });
      setProviders(providerList);
      setCustomProviders(customProviderList);

      const project = projectRes.project as unknown as Record<string, unknown>;
      const vb = (project.video_backend as string | undefined) ?? "";
      // Read T2I/I2I split fields; lazy-upgrade in project_manager populates both from legacy image_backend
      const ibt2i = (project.image_provider_t2i as string | undefined) ?? "";
      const ibi2i = (project.image_provider_i2i as string | undefined) ?? "";
      const rawAudio = project.video_generate_audio;
      const ao = typeof rawAudio === "boolean" ? rawAudio : null;
      const ts = (project.text_backend_script as string | undefined) ?? "";
      const to = (project.text_backend_overview as string | undefined) ?? "";
      const tst = (project.text_backend_style as string | undefined) ?? "";

      const rawAr = typeof project.aspect_ratio === "string" ? project.aspect_ratio : "";
      // Backend's get_aspect_ratio() falls back to "9:16" when unset (generation_tasks.py).
      // Mirror that here so the UI reflects the actually-effective ratio.
      const ar = rawAr || "9:16";
      const gm = normalizeMode(project.generation_mode);
      const dd = project.default_duration != null ? (project.default_duration as number) : null;

      setVideoBackend(vb);
      setImageBackendT2I(ibt2i);
      setImageBackendI2I(ibi2i);
      setAudioOverride(ao);
      setTextScript(ts);
      setTextOverview(to);
      setTextStyle(tst);
      setAspectRatio(ar);
      setGenerationMode(gm);
      setDefaultDuration(dd);
      setProjectTitle(typeof project.title === "string" ? project.title : "");

      // model_settings 的 key 以 effective backend（override ‖ global default）读写，
      // 与 handleSave 保持一致；legacy video_model_settings 作为旧项目兼容回退。
      const defaultVideo = configRes.settings?.default_video_backend ?? "";
      const defaultImageT2I =
        configRes.settings?.default_image_backend_t2i ||
        configRes.settings?.default_image_backend ||
        "";
      const effectiveVb = vb || defaultVideo;
      const effectiveIb = ibt2i || defaultImageT2I; // T2I treated as canonical for resolution
      const ms = (project.model_settings ?? {}) as Record<string, { resolution: string | null }>;
      const legacyVideo = (project.video_model_settings ?? {}) as Record<string, { resolution?: string | null }>;
      const vModelId = effectiveVb && effectiveVb.includes("/") ? effectiveVb.split("/")[1] : effectiveVb;
      const vRes: string | null =
        (effectiveVb ? (ms[effectiveVb]?.resolution ?? null) : null) ||
        (vModelId ? (legacyVideo[vModelId]?.resolution ?? null) : null) ||
        null;
      const iRes = effectiveIb ? (ms[effectiveIb]?.resolution ?? null) : null;
      setVideoResolution(vRes);
      setImageResolution(iRes);
      setModelSettings(ms);

      const derivedStyle = deriveStyleValue(project, projectName);
      setStyleValue(derivedStyle);
      initialStyleRef.current = derivedStyle;
      initialRef.current = {
        videoBackend: vb, imageBackendT2I: ibt2i, imageBackendI2I: ibi2i, audioOverride: ao,
        textScript: ts, textOverview: to, textStyle: tst,
        aspectRatio: ar, generationMode: gm, defaultDuration: dd,
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
    imageBackendT2I !== initialRef.current.imageBackendT2I ||
    imageBackendI2I !== initialRef.current.imageBackendI2I ||
    audioOverride !== initialRef.current.audioOverride ||
    textScript !== initialRef.current.textScript ||
    textOverview !== initialRef.current.textOverview ||
    textStyle !== initialRef.current.textStyle ||
    aspectRatio !== initialRef.current.aspectRatio ||
    generationMode !== initialRef.current.generationMode ||
    defaultDuration !== initialRef.current.defaultDuration ||
    videoResolution !== initialRef.current.videoResolution ||
    imageResolution !== initialRef.current.imageResolution ||
    styleIsDirty;

  useEffect(() => {
    if (!isDirty) return;
    const handler = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty]);

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

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      // resolution 的 key 用 effective backend（override ‖ global default），
      // 否则"跟随全局默认"路径下用户选的分辨率不会被写入。
      const effectiveVideo = videoBackend || globalDefaults.video || "";
      const effectiveImageT2I = imageBackendT2I || globalDefaults.imageT2I || "";
      const newModelSettings: Record<string, { resolution: string | null }> = { ...modelSettings };
      if (effectiveVideo) {
        newModelSettings[effectiveVideo] = { resolution: videoResolution };
      }
      if (effectiveImageT2I) {
        newModelSettings[effectiveImageT2I] = { resolution: imageResolution };
      }

      await API.updateProject(projectName, {
        video_backend: videoBackend || null,
        image_provider_t2i: imageBackendT2I || null,
        image_provider_i2i: imageBackendI2I || null,
        video_generate_audio: audioOverride,
        text_backend_script: textScript || null,
        text_backend_overview: textOverview || null,
        text_backend_style: textStyle || null,
        aspect_ratio: aspectRatio || undefined,
        generation_mode: generationMode,
        default_duration: defaultDuration,
        model_settings: newModelSettings,
      } as Record<string, unknown>);
      setModelSettings(newModelSettings);
      initialRef.current = {
        videoBackend, imageBackendT2I, imageBackendI2I, audioOverride,
        textScript, textOverview, textStyle,
        aspectRatio, generationMode, defaultDuration,
        videoResolution, imageResolution,
      };
      useAppStore.getState().pushToast(t("saved"), "success");
    } catch (e: unknown) {
      useAppStore.getState().pushToast(t("save_failed", { message: errMsg(e) }), "error");
    } finally {
      setSaving(false);
    }
  }, [modelSettings, videoBackend, imageBackendT2I, imageBackendI2I, audioOverride, textScript, textOverview, textStyle, aspectRatio, generationMode, defaultDuration, videoResolution, imageResolution, projectName, t, globalDefaults.video, globalDefaults.imageT2I]);

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col text-text"
      style={
        {
          background:
            "radial-gradient(900px 480px at 8% -10%, oklch(0.32 0.05 295 / 0.22), transparent 55%), radial-gradient(800px 460px at 100% 110%, oklch(0.26 0.04 260 / 0.22), transparent 55%), linear-gradient(180deg, var(--color-bg-grad-a), var(--color-bg-grad-b))",
        } as CSSProperties
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
              title={projectTitle || projectName}
            >
              {t("project_settings")}
              <span className="ml-2 align-middle font-mono text-[11.5px] font-medium uppercase tracking-[0.08em] text-text-3">
                {projectTitle || projectName}
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
                  value={{
                    videoBackend,
                    imageBackendT2I,
                    imageBackendI2I,
                    textBackendScript: textScript,
                    textBackendOverview: textOverview,
                    textBackendStyle: textStyle,
                    defaultDuration,
                    videoResolution,
                    imageResolution,
                  }}
                  onChange={(next) => {
                    setVideoBackend(next.videoBackend);
                    setImageBackendT2I(next.imageBackendT2I);
                    setImageBackendI2I(next.imageBackendI2I);
                    setTextScript(next.textBackendScript);
                    setTextOverview(next.textBackendOverview);
                    setTextStyle(next.textBackendStyle);
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
                  globalDefaults={{
                    video: globalDefaults.video,
                    imageT2I: globalDefaults.imageT2I ?? "",
                    imageI2I: globalDefaults.imageI2I ?? "",
                    textScript: globalDefaults.textScript ?? "",
                    textOverview: globalDefaults.textOverview ?? "",
                    textStyle: globalDefaults.textStyle ?? "",
                  }}
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

              {/* Generation mode */}
              <SectionCard kicker="Pipeline Mode">
                <fieldset>
                  <legend className="mb-2 block font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-3">
                    {t("generation_mode")}
                  </legend>
                  <GenerationModeSelector
                    value={generationMode}
                    onChange={setGenerationMode}
                  />
                </fieldset>
              </SectionCard>

              {/* Audio override */}
              <SectionCard kicker="Audio Channel">
                <div className="mb-2.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-3">
                  {t("generate_audio_label")}
                </div>
                <fieldset className="flex flex-wrap gap-x-5 gap-y-2">
                  <legend className="sr-only">{t("audio_settings_sr_label")}</legend>
                  <label className="inline-flex items-center gap-2 text-[12.5px] text-text-2">
                    <input
                      type="radio"
                      name="audio"
                      value=""
                      checked={audioOverride === null}
                      onChange={() => setAudioOverride(null)}
                      className="accent-[oklch(0.76_0.09_295)]"
                    />
                    {t("follow_global_default")}
                  </label>
                  <label className="inline-flex items-center gap-2 text-[12.5px] text-text-2">
                    <input
                      type="radio"
                      name="audio"
                      value="true"
                      checked={audioOverride === true}
                      onChange={() => setAudioOverride(true)}
                      className="accent-[oklch(0.76_0.09_295)]"
                    />
                    {t("enabled_label")}
                  </label>
                  <label className="inline-flex items-center gap-2 text-[12.5px] text-text-2">
                    <input
                      type="radio"
                      name="audio"
                      value="false"
                      checked={audioOverride === false}
                      onChange={() => setAudioOverride(false)}
                      className="accent-[oklch(0.76_0.09_295)]"
                    />
                    {t("disabled_label")}
                  </label>
                </fieldset>
              </SectionCard>
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
        tone="warning"
        title={t("unsaved_changes_confirm")}
        confirmLabel={t("common:confirm")}
        cancelLabel={t("common:cancel")}
        onCancel={() => setPendingNavigation(null)}
        onConfirm={confirmDiscardAndNavigate}
      />
    </div>
  );
}
