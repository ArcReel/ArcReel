import { useParams, useLocation } from "wouter";
import { useState, useEffect, useCallback } from "react";
import { ArrowLeft } from "lucide-react";
import { API } from "@/api";
import { ProviderModelSelect } from "@/components/ui/ProviderModelSelect";

const PROVIDER_NAMES: Record<string, string> = {
  "gemini-aistudio": "AI Studio",
  "gemini-vertex": "Vertex AI",
  "seedance": "Seedance",
  "grok": "Grok",
};

export function ProjectSettingsPage() {
  const params = useParams<{ projectName: string }>();
  const projectName = params.projectName || "";
  const [, navigate] = useLocation();

  const [options, setOptions] = useState<{
    video_backends: string[];
    image_backends: string[];
  } | null>(null);
  const [globalDefaults, setGlobalDefaults] = useState<{
    video: string;
    image: string;
  }>({ video: "", image: "" });

  // Project-level overrides (from project.json)
  // "" means "follow global default"
  const [videoBackend, setVideoBackend] = useState<string>("");
  const [imageBackend, setImageBackend] = useState<string>("");
  const [audioOverride, setAudioOverride] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedOk, setSavedOk] = useState(false);

  useEffect(() => {
    let disposed = false;

    Promise.all([
      API.getSystemConfig(),
      API.getProject(projectName),
    ]).then(([configRes, projectRes]) => {
      if (disposed) return;

      setOptions({
        video_backends: configRes.options?.video_backends ?? [],
        image_backends: configRes.options?.image_backends ?? [],
      });
      setGlobalDefaults({
        video: configRes.settings?.default_video_backend ?? "",
        image: configRes.settings?.default_image_backend ?? "",
      });

      const project = projectRes.project as unknown as Record<string, unknown>;
      setVideoBackend((project.video_backend as string | undefined) ?? "");
      setImageBackend((project.image_backend as string | undefined) ?? "");
      const rawAudio = project.video_generate_audio;
      setAudioOverride(typeof rawAudio === "boolean" ? rawAudio : null);
    });

    return () => { disposed = true; };
  }, [projectName]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    setSaveError(null);
    setSavedOk(false);
    try {
      await API.updateProject(projectName, {
        video_backend: videoBackend || undefined,
        image_backend: imageBackend || undefined,
        video_generate_audio: audioOverride,
      });
      setSavedOk(true);
      setTimeout(() => setSavedOk(false), 2000);
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }, [videoBackend, imageBackend, audioOverride, projectName]);

  return (
    <div className="fixed inset-0 z-50 bg-gray-950 overflow-y-auto">
      {/* Header */}
      <div className="sticky top-0 z-10 flex items-center gap-3 border-b border-gray-800 bg-gray-950/95 px-6 py-4 backdrop-blur">
        <button
          onClick={() => navigate(`/app/projects/${projectName}`)}
          className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-800 hover:text-gray-200"
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
                providerNames={PROVIDER_NAMES}
                onChange={setVideoBackend}
                allowDefault
                defaultHint={
                  globalDefaults.video ? `当前全局: ${globalDefaults.video}` : undefined
                }
              />
            </div>

            {/* Image model override */}
            <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
              <div className="mb-3 text-sm font-medium text-gray-100">图片模型</div>
              <ProviderModelSelect
                value={imageBackend}
                options={options.image_backends}
                providerNames={PROVIDER_NAMES}
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
          </>
        )}

        {!options && (
          <div className="text-sm text-gray-500">加载配置中…</div>
        )}

        {/* Error / success feedback */}
        {saveError && (
          <p className="text-sm text-red-400">{saveError}</p>
        )}
        {savedOk && (
          <p className="text-sm text-green-400">已保存</p>
        )}

        {/* Actions */}
        <div className="flex gap-3">
          <button
            onClick={handleSave}
            disabled={saving}
            className="rounded-lg bg-indigo-600 px-6 py-2 text-sm text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {saving ? "保存中…" : "保存"}
          </button>
          <button
            onClick={() => navigate(`/app/projects/${projectName}`)}
            className="rounded-lg border border-gray-700 px-6 py-2 text-sm text-gray-300 hover:bg-gray-800"
          >
            取消
          </button>
        </div>
      </div>
    </div>
  );
}
