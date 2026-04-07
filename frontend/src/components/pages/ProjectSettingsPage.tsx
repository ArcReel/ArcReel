import { useParams, useLocation } from "wouter";
import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { ArrowLeft } from "lucide-react";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { ProviderModelSelect } from "@/components/ui/ProviderModelSelect";
import { PROVIDER_NAMES } from "@/components/ui/ProviderIcon";
import { getProviderModels, getCustomProviderModels, lookupSupportedDurations, DEFAULT_DURATIONS } from "@/utils/provider-models";
import type { CustomProviderInfo, ProviderInfo } from "@/types";

export function ProjectSettingsPage() {
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
  }>({ video: "", image: "" });

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
  const [defaultDuration, setDefaultDuration] = useState<number | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [customProviders, setCustomProviders] = useState<CustomProviderInfo[]>([]);
  const [saving, setSaving] = useState(false);
  const initialRef = useRef({
    videoBackend: "", imageBackend: "", audioOverride: null as boolean | null,
    textScript: "", textOverview: "", textStyle: "",
    aspectRatio: "", defaultDuration: null as number | null,
  });

  useEffect(() => {
    let disposed = false;

    Promise.all([
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

      const ar = typeof project.aspect_ratio === "string"
        ? project.aspect_ratio
        : "";
      const dd = project.default_duration != null ? (project.default_duration as number) : null;

      setVideoBackend(vb);
      setImageBackend(ib);
      setAudioOverride(ao);
      setTextScript(ts);
      setTextOverview(to);
      setTextStyle(tst);
      setAspectRatio(ar);
      setDefaultDuration(dd);
      initialRef.current = {
        videoBackend: vb, imageBackend: ib, audioOverride: ao,
        textScript: ts, textOverview: to, textStyle: tst,
        aspectRatio: ar, defaultDuration: dd,
      };
    });

    return () => { disposed = true; };
  }, [projectName]);

  const effectiveVideoBackend = videoBackend || globalDefaults.video;
  const supportedDurations = useMemo(
    () => lookupSupportedDurations(providers, effectiveVideoBackend, customProviders),
    [providers, effectiveVideoBackend, customProviders],
  );

  // Derive effective default duration during render — if current value
  // is not in the model's supported list, treat it as "auto" (null).
  const effectiveDefaultDuration =
    supportedDurations && defaultDuration !== null && !supportedDurations.includes(defaultDuration)
      ? null
      : defaultDuration;

  const handleVideoBackendChange = useCallback((value: string) => {
    setVideoBackend(value);
    // When video model changes, reset default duration so the UI
    // re-evaluates against the new model's supported durations.
    const effective = value || globalDefaults.video;
    const durations = lookupSupportedDurations(providers, effective, customProviders);
    if (durations && defaultDuration !== null && !durations.includes(defaultDuration)) {
      setDefaultDuration(null);
    }
  }, [globalDefaults.video, providers, customProviders, defaultDuration]);

  const isDirty =
    videoBackend !== initialRef.current.videoBackend ||
    imageBackend !== initialRef.current.imageBackend ||
    audioOverride !== initialRef.current.audioOverride ||
    textScript !== initialRef.current.textScript ||
    textOverview !== initialRef.current.textOverview ||
    textStyle !== initialRef.current.textStyle ||
    aspectRatio !== initialRef.current.aspectRatio ||
    defaultDuration !== initialRef.current.defaultDuration;

  useEffect(() => {
    if (!isDirty) return;
    const handler = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty]);

  const guardedNavigate = useCallback((path: string) => {
    if (isDirty && !window.confirm("有未保存的修改，确定要离开吗？")) return;
    navigate(path);
  }, [isDirty, navigate]);

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
        default_duration: defaultDuration,
      } as Record<string, unknown>);
      initialRef.current = {
        videoBackend, imageBackend, audioOverride,
        textScript, textOverview, textStyle,
        aspectRatio, defaultDuration,
      };
      useAppStore.getState().pushToast("已保存", "success");
    } catch (e: unknown) {
      useAppStore.getState().pushToast(e instanceof Error ? e.message : "保存失败", "error");
    } finally {
      setSaving(false);
    }
  }, [videoBackend, imageBackend, audioOverride, textScript, textOverview, textStyle, aspectRatio, defaultDuration, projectName]);

  return (
    <div className="fixed inset-0 z-50 bg-gray-950 overflow-y-auto">
      {/* Header */}
      <div className="sticky top-0 z-10 flex items-center gap-3 border-b border-gray-800 bg-gray-950/95 px-6 py-4 backdrop-blur">
        <button
          onClick={() => guardedNavigate(`/app/projects/${projectName}`)}
          className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-800 hover:text-gray-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          aria-label="返回项目"
        >
          <ArrowLeft className="h-5 w-5" />
        </button>
        <h1 className="text-lg font-semibold text-gray-100">项目设置</h1>
      </div>

      {/* Content */}
      <div className="mx-auto max-w-2xl px-6 py-8 space-y-6">
        <div>
          <h2 className="text-lg font-semibold text-gray-100">模型配置</h2>
          <p className="mt-1 text-sm text-gray-500">
            为此项目单独选择生成模型，留空则跟随全局默认
          </p>
        </div>

        {options && (
          <>
            {/* Video model override */}
            <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
              <div className="mb-3 text-sm font-medium text-gray-100">视频模型</div>
              <ProviderModelSelect
                value={videoBackend}
                options={options.video_backends}
                providerNames={allProviderNames}
                onChange={handleVideoBackendChange}
                allowDefault
                defaultHint={
                  globalDefaults.video ? `当前全局: ${globalDefaults.video}` : undefined
                }
              />
            </div>

            {/* Aspect ratio */}
            <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
              <fieldset>
                <legend className="mb-3 text-sm font-medium text-gray-100">画面比例</legend>
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
                        onChange={() => setAspectRatio(ar)}
                        className="sr-only"
                      />
                      {ar === "9:16" ? "竖屏 9:16" : "横屏 16:9"}
                    </label>
                  ))}
                </div>
              </fieldset>
            </div>

            {/* Default duration */}
            <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
              <div className="mb-3 text-sm font-medium text-gray-100">默认时长</div>
              <p className="mb-2 text-xs text-gray-500">
                新分镜的默认视频时长，「自动」表示由 AI 根据内容决定
              </p>
              <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="默认时长选择">
                <button
                  type="button"
                  role="radio"
                  aria-checked={effectiveDefaultDuration === null}
                  onClick={() => setDefaultDuration(null)}
                  className={`rounded-lg border px-3 py-1.5 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
                    effectiveDefaultDuration === null
                      ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
                      : "border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600"
                  }`}
                >
                  自动
                </button>
                {(supportedDurations ?? DEFAULT_DURATIONS).map((d) => (
                  <button
                    key={d}
                    type="button"
                    role="radio"
                    aria-checked={effectiveDefaultDuration === d}
                    onClick={() => setDefaultDuration(d)}
                    className={`rounded-lg border px-3 py-1.5 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
                      effectiveDefaultDuration === d
                        ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
                        : "border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600"
                    }`}
                  >
                    {d}s
                  </button>
                ))}
              </div>
            </div>

            {/* Image model override */}
            <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
              <div className="mb-3 text-sm font-medium text-gray-100">图片模型</div>
              <ProviderModelSelect
                value={imageBackend}
                options={options.image_backends}
                providerNames={allProviderNames}
                onChange={setImageBackend}
                allowDefault
                defaultHint={
                  globalDefaults.image ? `当前全局: ${globalDefaults.image}` : undefined
                }
              />
            </div>

            {/* Audio override */}
            <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
              <div className="mb-3 text-sm font-medium text-gray-100">生成音频</div>
              <fieldset className="flex gap-4">
                <legend className="sr-only">生成音频设置</legend>
                <label className="flex items-center gap-2 text-sm text-gray-300">
                  <input type="radio" name="audio" value="" checked={audioOverride === null}
                    onChange={() => setAudioOverride(null)} />
                  跟随全局默认
                </label>
                <label className="flex items-center gap-2 text-sm text-gray-300">
                  <input type="radio" name="audio" value="true" checked={audioOverride === true}
                    onChange={() => setAudioOverride(true)} />
                  开启
                </label>
                <label className="flex items-center gap-2 text-sm text-gray-300">
                  <input type="radio" name="audio" value="false" checked={audioOverride === false}
                    onChange={() => setAudioOverride(false)} />
                  关闭
                </label>
              </fieldset>
            </div>
            {/* Text model overrides */}
            <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
              <div className="mb-3 text-sm font-medium text-gray-100">文本模型</div>
              <p className="mb-2 text-xs text-gray-500">按任务类型覆盖，留空跟随全局默认</p>
              <div className="space-y-3">
                {([
                  [textScript, setTextScript, "剧本生成"] as const,
                  [textOverview, setTextOverview, "概述生成"] as const,
                  [textStyle, setTextStyle, "风格分析"] as const,
                ]).map(([value, setter, label]) => (
                  <div key={label}>
                    <div className="mb-1 text-xs text-gray-400">{label}</div>
                    <ProviderModelSelect
                      value={value}
                      options={options.text_backends}
                      providerNames={allProviderNames}
                      onChange={setter}
                      allowDefault
                      defaultHint="跟随全局默认"
                      aria-label={label}
                    />
                  </div>
                ))}
              </div>
            </div>
          </>
        )}

        {!options && (
          <div className="text-sm text-gray-500">加载配置中…</div>
        )}

        {/* Actions */}
        <div className="flex gap-3">
          <button
            onClick={handleSave}
            disabled={saving}
            className="rounded-lg bg-indigo-600 px-6 py-2 text-sm text-white hover:bg-indigo-500 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 focus-visible:ring-offset-gray-950"
          >
            {saving ? "保存中…" : "保存"}
          </button>
          <button
            onClick={() => guardedNavigate(`/app/projects/${projectName}`)}
            className="rounded-lg border border-gray-700 px-6 py-2 text-sm text-gray-300 hover:bg-gray-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 focus-visible:ring-offset-gray-950"
          >
            取消
          </button>
        </div>
      </div>
    </div>
  );
}
