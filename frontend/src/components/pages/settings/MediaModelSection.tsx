import { useState, useEffect, useCallback } from "react";
import { API } from "@/api";
import { ProviderModelSelect } from "@/components/ui/ProviderModelSelect";
import { useAppStore } from "@/stores/app-store";

// ---------------------------------------------------------------------------
// Provider display names
// ---------------------------------------------------------------------------

const PROVIDER_NAMES: Record<string, string> = {
  "gemini-aistudio": "AI Studio",
  "gemini-vertex": "Vertex AI",
  seedance: "Seedance",
  grok: "Grok",
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function MediaModelSection() {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [settings, setSettings] = useState<Record<string, any> | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [options, setOptions] = useState<Record<string, any> | null>(null);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);

  const fetchConfig = useCallback(async () => {
    // The new API returns { settings: {...}, options: {...} }
    // The old API returns { config: {...}, options: {...} }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const res = (await API.getSystemConfig()) as any;
    setSettings(res.settings ?? res.config ?? null);
    setOptions(res.options ?? null);
    setDraft({});
  }, []);

  useEffect(() => {
    void fetchConfig();
  }, [fetchConfig]);

  const handleSave = useCallback(async () => {
    if (Object.keys(draft).length === 0) return;
    setSaving(true);
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      await (API.updateSystemConfig as any)(draft);
      await fetchConfig();
      useAppStore.getState().pushToast("媒体模型配置已保存", "success");
    } catch (err) {
      useAppStore.getState().pushToast(`保存失败: ${(err as Error).message}`, "error");
    } finally {
      setSaving(false);
    }
  }, [draft, fetchConfig]);

  if (!settings || !options) {
    return <div className="p-6 text-sm text-gray-500">加载中…</div>;
  }

  const isDirty = Object.keys(draft).length > 0;

  const videoBackends: string[] = options.video_backends ?? [];
  const imageBackends: string[] = options.image_backends ?? [];

  const currentVideo = (draft.default_video_backend as string | undefined) ??
    (settings.default_video_backend as string | null | undefined) ??
    "";
  const currentImage = (draft.default_image_backend as string | undefined) ??
    (settings.default_image_backend as string | null | undefined) ??
    "";
  const currentAudio = (draft.video_generate_audio as boolean | undefined) ??
    (settings.video_generate_audio as boolean | undefined) ??
    false;

  return (
    <div className="space-y-6 p-6">
      {/* Section heading */}
      <div>
        <h3 className="text-lg font-semibold text-gray-100">图片 / 视频模型</h3>
        <p className="mt-1 text-sm text-gray-500">设置全局默认的生成模型，项目内可单独覆盖</p>
      </div>

      {/* Video backend selector */}
      <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
        <div className="mb-3 text-sm font-medium text-gray-100">默认视频模型</div>
        {videoBackends.length > 0 ? (
          <ProviderModelSelect
            value={currentVideo}
            options={videoBackends}
            providerNames={PROVIDER_NAMES}
            onChange={(v) => setDraft((prev) => ({ ...prev, default_video_backend: v }))}
          />
        ) : (
          <div className="rounded-lg border border-gray-800 bg-gray-900/60 px-3 py-2 text-sm text-gray-500">
            暂无可用视频供应商，请先在「供应商」页面配置 API 密钥
          </div>
        )}

        {/* Audio toggle */}
        <label className="mt-3 flex cursor-pointer items-center gap-2 text-sm text-gray-300">
          <input
            type="checkbox"
            checked={currentAudio}
            onChange={(e) =>
              setDraft((prev) => ({ ...prev, video_generate_audio: e.target.checked }))
            }
            className="rounded border-gray-600 bg-gray-800"
          />
          生成音频
          <span className="text-xs text-gray-500">（仅部分供应商支持）</span>
        </label>
      </div>

      {/* Image backend selector */}
      <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
        <div className="mb-3 text-sm font-medium text-gray-100">默认图片模型</div>
        {imageBackends.length > 0 ? (
          <ProviderModelSelect
            value={currentImage}
            options={imageBackends}
            providerNames={PROVIDER_NAMES}
            onChange={(v) => setDraft((prev) => ({ ...prev, default_image_backend: v }))}
          />
        ) : (
          <div className="rounded-lg border border-gray-800 bg-gray-900/60 px-3 py-2 text-sm text-gray-500">
            暂无可用图片供应商，请先在「供应商」页面配置 API 密钥
          </div>
        )}
      </div>

      {/* Save / reset buttons */}
      {isDirty && (
        <div className="flex gap-3">
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving ? "保存中…" : "保存"}
          </button>
          <button
            type="button"
            onClick={() => setDraft({})}
            className="rounded-lg border border-gray-700 px-4 py-2 text-sm text-gray-300 transition-colors hover:bg-gray-800"
          >
            重置
          </button>
        </div>
      )}
    </div>
  );
}
