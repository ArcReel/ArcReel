
import { useState, useEffect, useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useWarnUnsaved } from "@/hooks/useWarnUnsaved";
import { API } from "@/api";
import type { SystemConfigSettings, SystemConfigOptions, SystemConfigPatch } from "@/types/system";
import { ProviderModelSelect } from "@/components/ui/ProviderModelSelect";
import { PROVIDER_NAMES } from "@/components/ui/ProviderIcon";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function MediaModelSection() {
  const { t } = useTranslation();
  
  const TEXT_MODEL_FIELDS = useMemo(() => [
    ["text_backend_script", t("剧本生成")],
    ["text_backend_overview", t("概述生成")],
    ["text_backend_style", t("风格分析")],
  ] as const, [t]);

  const [settings, setSettings] = useState<SystemConfigSettings | null>(null);
  const [options, setOptions] = useState<SystemConfigOptions | null>(null);
  const [draft, setDraft] = useState<SystemConfigPatch>({});
  const [saving, setSaving] = useState(false);

  const isDirty = Object.keys(draft).length > 0;
  useWarnUnsaved(isDirty);

  const allProviderNames = useMemo(
    () => ({ ...PROVIDER_NAMES, ...(options?.provider_names ?? {}) }),
    [options],
  );

  const fetchConfig = useCallback(async () => {
    const res = await API.getSystemConfig();
    setSettings(res.settings);
    setOptions(res.options);
    setDraft({});
  }, []);

  useEffect(() => {
    void fetchConfig();
  }, [fetchConfig]);

  const handleSave = useCallback(async () => {
    if (Object.keys(draft).length === 0) return;
    setSaving(true);
    try {
      await API.updateSystemConfig(draft);
      await fetchConfig();
      void useConfigStatusStore.getState().refresh();
      useAppStore.getState().pushToast(t("媒体模型配置已保存"), "success");
    } catch (err) {
      useAppStore.getState().pushToast(`${t("保存失败: ")}${(err as Error).message}`, "error");
    } finally {
      setSaving(false);
    }
  }, [draft, fetchConfig, t]);

  if (!settings || !options) {
    return <div className="p-6 text-sm text-gray-500">{t("加载中…")}</div>;
  }

  const videoBackends: string[] = options.video_backends ?? [];
  const imageBackends: string[] = options.image_backends ?? [];
  const textBackends: string[] = options.text_backends ?? [];

  const currentVideo = draft.default_video_backend ?? settings.default_video_backend ?? "";
  const currentImage = draft.default_image_backend ?? settings.default_image_backend ?? "";
  const currentAudio = draft.video_generate_audio ?? settings.video_generate_audio ?? false;

  return (
    <div className="space-y-6 p-6">
      {/* Section heading */}
      <div>
        <h3 className="text-lg font-semibold text-gray-100">{t("模型选择")}</h3>
        <p className="mt-1 text-sm text-gray-500">{t("设置全局默认的生成模型，项目内可单独覆盖")}</p>
      </div>

      {/* Video backend selector */}
      <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
        <div className="mb-3 text-sm font-medium text-gray-100">{t("默认视频模型")}</div>
        {videoBackends.length > 0 ? (
          <ProviderModelSelect
            value={currentVideo}
            options={videoBackends}
            providerNames={allProviderNames}
            onChange={(v) => setDraft((prev) => ({ ...prev, default_video_backend: v }))}
            allowDefault
            defaultLabel={t("自动选择")}
            defaultHint={t("自动")}
          />
        ) : (
          <div className="rounded-lg border border-gray-800 bg-gray-900/60 px-3 py-2 text-sm text-gray-500">
            {t("暂无可用视频供应商，请先在「供应商」页面配置 API 密钥")}
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
          {t("生成音频")}
          <span className="text-xs text-gray-500">{t("（仅部分供应商支持）")}</span>
        </label>
      </div>

      {/* Image backend selector */}
      <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
        <div className="mb-3 text-sm font-medium text-gray-100">{t("默认图片模型")}</div>
        {imageBackends.length > 0 ? (
          <ProviderModelSelect
            value={currentImage}
            options={imageBackends}
            providerNames={allProviderNames}
            onChange={(v) => setDraft((prev) => ({ ...prev, default_image_backend: v }))}
            allowDefault
            defaultLabel={t("自动选择")}
            defaultHint={t("自动")}
          />
        ) : (
          <div className="rounded-lg border border-gray-800 bg-gray-900/60 px-3 py-2 text-sm text-gray-500">
            {t("暂无可用图片供应商，请先在「供应商」页面配置 API 密钥")}
          </div>
        )}
      </div>

      {/* Text backend selectors */}
      <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
        <div className="mb-3 text-sm font-medium text-gray-100">{t("文本模型")}</div>
        <p className="mb-3 text-xs text-gray-500">{t("按任务类型配置文本模型，留空表示自动选择")}</p>

        {textBackends.length > 0 ? (
          <div className="space-y-3">
            {TEXT_MODEL_FIELDS.map(([key, label]) => (
              <div key={key}>
                <div className="mb-1 text-xs text-gray-400">{label}</div>
                <ProviderModelSelect
                  value={(draft[key] ?? settings[key] ?? "") as string}
                  options={textBackends}
                  providerNames={allProviderNames}
                  onChange={(v) => setDraft((prev) => ({ ...prev, [key]: v }))}
                  allowDefault
                  defaultHint={t("自动")}
                  aria-label={label}
                />
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded-lg border border-gray-800 bg-gray-900/60 px-3 py-2 text-sm text-gray-500">
            {t("暂无可用文本供应商，请先在「供应商」页面配置 API 密钥")}
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
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-indigo-500/60 focus-visible:outline-none"
          >
            {saving ? t("保存中…") : t("保存")}
          </button>
          <button
            type="button"
            onClick={() => setDraft({})}
            className="rounded-lg border border-gray-700 px-4 py-2 text-sm text-gray-300 transition-colors hover:bg-gray-800 focus-visible:ring-2 focus-visible:ring-indigo-500/60 focus-visible:outline-none"
          >
            {t("重置")}
          </button>
        </div>
      )}
    </div>
  );
}
