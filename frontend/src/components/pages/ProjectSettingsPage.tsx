import { useParams, useLocation } from "wouter";
import { errMsg, voidCall, voidPromise } from "@/utils/async";
import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft, Loader2 } from "lucide-react";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { PROVIDER_NAMES } from "@/components/ui/ProviderIcon";
import { getProviderModels, getCustomProviderModels } from "@/utils/provider-models";
import { ModelConfigSection } from "@/components/shared/ModelConfigSection";
import { StylePicker, type StylePickerValue } from "@/components/shared/StylePicker";
import { DEFAULT_TEMPLATE_ID, STYLE_TEMPLATES } from "@/data/style-templates";
import type { CustomProviderInfo, ProviderInfo } from "@/types";
import { GenerationModeSelector } from "@/components/shared/GenerationModeSelector";
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
    image: string;
    textScript: string;
    textOverview: string;
    textStyle: string;
  }>({ video: "", image: "", textScript: "", textOverview: "", textStyle: "" });

  const allProviderNames = useMemo(
    () => ({ ...PROVIDER_NAMES, ...(options?.provider_names ?? {}) }),
    [options],
  );

  // Project-level overrides (from project.json)
  // "" means "follow global default"
  const [videoBackend, setVideoBackend] = useState<string>("");
  const [imageBackend, setImageBackend] = useState<string>("");
  const [audioOverride, setAudioOverride] = useState<boolean | null>(null);
  const [textScript, setTextScript] = useState<string>("");
  const [textOverview, setTextOverview] = useState<string>("");
  const [textStyle, setTextStyle] = useState<string>("");
  const [aspectRatio, setAspectRatio] = useState<string>("");
  const [generationMode, setGenerationMode] = useState<GenerationMode>("storyboard");
  const [defaultDuration, setDefaultDuration] = useState<number | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [customProviders, setCustomProviders] = useState<CustomProviderInfo[]>([]);
  const [saving, setSaving] = useState(false);

  // ── Style picker state (independent save flow) ─────────────────────────────
  const [styleValue, setStyleValue] = useState<StylePickerValue | null>(null);
  const [savingStyle, setSavingStyle] = useState(false);
  const initialRef = useRef({
    videoBackend: "", imageBackend: "", audioOverride: null as boolean | null,
    textScript: "", textOverview: "", textStyle: "",
    aspectRatio: "", generationMode: "storyboard" as GenerationMode,
    defaultDuration: null as number | null,
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
        image: configRes.settings?.default_image_backend ?? "",
        textScript: configRes.settings?.text_backend_script ?? "",
        textOverview: configRes.settings?.text_backend_overview ?? "",
        textStyle: configRes.settings?.text_backend_style ?? "",
      });
      setProviders(providerList);
      setCustomProviders(customProviderList);

      const project = projectRes.project as unknown as Record<string, unknown>;
      const vb = (project.video_backend as string | undefined) ?? "";
      const ib = (project.image_backend as string | undefined) ?? "";
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
      setImageBackend(ib);
      setAudioOverride(ao);
      setTextScript(ts);
      setTextOverview(to);
      setTextStyle(tst);
      setAspectRatio(ar);
      setGenerationMode(gm);
      setDefaultDuration(dd);
      const derivedStyle = deriveStyleValue(project, projectName);
      setStyleValue(derivedStyle);
      initialStyleRef.current = derivedStyle;
      initialRef.current = {
        videoBackend: vb, imageBackend: ib, audioOverride: ao,
        textScript: ts, textOverview: to, textStyle: tst,
        aspectRatio: ar, generationMode: gm, defaultDuration: dd,
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
    imageBackend !== initialRef.current.imageBackend ||
    audioOverride !== initialRef.current.audioOverride ||
    textScript !== initialRef.current.textScript ||
    textOverview !== initialRef.current.textOverview ||
    textStyle !== initialRef.current.textStyle ||
    aspectRatio !== initialRef.current.aspectRatio ||
    generationMode !== initialRef.current.generationMode ||
    defaultDuration !== initialRef.current.defaultDuration ||
    styleIsDirty;

  useEffect(() => {
    if (!isDirty) return;
    const handler = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty]);

  const guardedNavigate = useCallback((path: string) => {
    if (isDirty && !window.confirm(t("unsaved_changes_confirm"))) return;
    navigate(path);
  }, [isDirty, navigate, t]);

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
      useAppStore.getState().pushToast(errMsg(e, t("save_failed")), "error");
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
      await API.updateProject(projectName, {
        video_backend: videoBackend || null,
        image_backend: imageBackend || null,
        video_generate_audio: audioOverride,
        text_backend_script: textScript || null,
        text_backend_overview: textOverview || null,
        text_backend_style: textStyle || null,
        aspect_ratio: aspectRatio || undefined,
        generation_mode: generationMode,
        default_duration: defaultDuration,
      } as Record<string, unknown>);
      initialRef.current = {
        videoBackend, imageBackend, audioOverride,
        textScript, textOverview, textStyle,
        aspectRatio, generationMode, defaultDuration,
      };
      useAppStore.getState().pushToast(t("saved"), "success");
    } catch (e: unknown) {
      useAppStore.getState().pushToast(errMsg(e, t("save_failed")), "error");
    } finally {
      setSaving(false);
    }
  }, [videoBackend, imageBackend, audioOverride, textScript, textOverview, textStyle, aspectRatio, generationMode, defaultDuration, projectName, t]);

  return (
    <div className="fixed inset-0 z-50 bg-gray-950 overflow-y-auto">
      {/* Header */}
      <div className="sticky top-0 z-10 flex items-center gap-3 border-b border-gray-800 bg-gray-950/95 px-6 py-4 backdrop-blur">
        <button
          onClick={() => guardedNavigate(`/app/projects/${projectName}`)}
          className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-800 hover:text-gray-200 focus-ring"
          aria-label={t("back_to_project")}
        >
          <ArrowLeft className="h-5 w-5" />
        </button>
        <h1 className="text-lg font-semibold text-gray-100">{t("project_settings")}</h1>
      </div>

      {/* Content */}
      <div className="mx-auto max-w-2xl px-6 py-8 space-y-6">
        <div>
          <h2 className="text-lg font-semibold text-gray-100">{t("model_config")}</h2>
          <p className="mt-1 text-sm text-gray-500">
            {t("model_config_project_desc")}
          </p>
        </div>

        {/* Style picker (independent save flow, mutually exclusive template / custom) */}
        {styleValue && (
          <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4 space-y-3">
            <div className="text-sm font-medium text-gray-100">{t("project_style_section_title")}</div>
            <StylePicker value={styleValue} onChange={setStyleValue} />
            <div className="flex items-center gap-3 pt-2 border-t border-gray-800">
              <button
                type="button"
                onClick={voidPromise(handleSaveStyle)}
                disabled={isStyleSaveDisabled}
                className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 focus-visible:ring-offset-gray-950"
              >
                {savingStyle && <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin motion-reduce:animate-none" />}
                {savingStyle ? t("style_saving") : t("style_save")}
              </button>
              {hasInitialStyle && !isStyleCleared && !savingStyle && (
                <button
                  type="button"
                  onClick={handleClearStyle}
                  className="text-sm text-gray-400 hover:text-gray-200 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 focus-visible:ring-offset-gray-950 rounded"
                >
                  {t("style_clear")}
                </button>
              )}
              {isStyleCleared && !savingStyle && styleIsDirty && (
                <p className="text-xs text-gray-500">{t("style_cleared_hint")}</p>
              )}
            </div>
          </div>
        )}

        {options && (
          <>
            {/* Model config (video + duration + image + text) */}
            <ModelConfigSection
              value={{
                videoBackend,
                imageBackend,
                textBackendScript: textScript,
                textBackendOverview: textOverview,
                textBackendStyle: textStyle,
                defaultDuration,
              }}
              onChange={(next) => {
                setVideoBackend(next.videoBackend);
                setImageBackend(next.imageBackend);
                setTextScript(next.textBackendScript);
                setTextOverview(next.textBackendOverview);
                setTextStyle(next.textBackendStyle);
                setDefaultDuration(next.defaultDuration);
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
                image: globalDefaults.image,
                textScript: globalDefaults.textScript ?? "",
                textOverview: globalDefaults.textOverview ?? "",
                textStyle: globalDefaults.textStyle ?? "",
              }}
            />

            {/* Aspect ratio */}
            <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
              <fieldset>
                <legend className="mb-3 text-sm font-medium text-gray-100">{t("aspect_ratio_label")}</legend>
                <div className="flex gap-3">
                  {(["9:16", "16:9"] as const).map((ar) => (
                    <label
                      key={ar}
                      className={`flex-1 cursor-pointer rounded-lg border px-3 py-2 text-center text-sm transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-indigo-500 ${
                        aspectRatio === ar
                          ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
                          : "border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600"
                      }`}
                    >
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
                      {ar === "9:16" ? t("portrait_9_16") : t("landscape_16_9")}
                    </label>
                  ))}
                </div>
              </fieldset>
            </div>

            {/* Generation mode */}
            <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
              <fieldset>
                <legend className="mb-1 text-sm font-medium text-gray-100">{t("generation_mode")}</legend>
                <GenerationModeSelector
                  value={generationMode}
                  onChange={setGenerationMode}
                />
              </fieldset>
            </div>

            {/* Audio override */}
            <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
              <div className="mb-3 text-sm font-medium text-gray-100">{t("generate_audio_label")}</div>
              <fieldset className="flex gap-4">
                <legend className="sr-only">{t("audio_settings_sr_label")}</legend>
                <label className="flex items-center gap-2 text-sm text-gray-300">
                  <input type="radio" name="audio" value="" checked={audioOverride === null}
                    onChange={() => setAudioOverride(null)} />
                  {t("follow_global_default")}
                </label>
                <label className="flex items-center gap-2 text-sm text-gray-300">
                  <input type="radio" name="audio" value="true" checked={audioOverride === true}
                    onChange={() => setAudioOverride(true)} />
                  {t("enabled_label")}
                </label>
                <label className="flex items-center gap-2 text-sm text-gray-300">
                  <input type="radio" name="audio" value="false" checked={audioOverride === false}
                    onChange={() => setAudioOverride(false)} />
                  {t("disabled_label")}
                </label>
              </fieldset>
            </div>
          </>
        )}

        {!options && (
          <div className="text-sm text-gray-500">{t("loading_config")}</div>
        )}

        {/* Actions */}
        <div className="flex gap-3">
          <button
            onClick={voidPromise(handleSave)}
            disabled={saving}
            className="rounded-lg bg-indigo-600 px-6 py-2 text-sm text-white hover:bg-indigo-500 disabled:opacity-50 focus-ring focus-visible:ring-offset-2 focus-visible:ring-offset-gray-950"
          >
            {saving ? t("common:saving") : t("common:save")}
          </button>
          <button
            onClick={() => guardedNavigate(`/app/projects/${projectName}`)}
            className="rounded-lg border border-gray-700 px-6 py-2 text-sm text-gray-300 hover:bg-gray-800 focus-ring focus-visible:ring-offset-2 focus-visible:ring-offset-gray-950"
          >
            {t("common:cancel")}
          </button>
        </div>
      </div>
    </div>
  );
}
