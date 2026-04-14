
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
  const { t } = useTranslation("dashboard");
  
  const TEXT_MODEL_FIELDS = useMemo(() => [
    ["text_backend_script", t("script_generation")],
    ["text_backend_overview", t("overview_generation")],
    ["text_backend_style", t("style_analysis")],
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
      useAppStore.getState().pushToast(t("media_config_saved"), "success");
    } catch (err) {
      useAppStore.getState().pushToast(`${t("save_failed")}${(err as Error).message}`, "error");
    } finally {
      setSaving(false);
    }
  }, [draft, fetchConfig, t]);

  if (!settings || !options) {
    return <div className="p-6 text-sm text-gray-500">{t("common:loading")}</div>;
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
        <h3 className="text-lg font-semibold text-gray-100">{t("model_selection")}</h3>
        <p className="mt-1 text-sm text-gray-500">{t("model_selection_desc")}</p>
      </div>

      {/* Video backend selector */}
      <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
        <div className="mb-3 text-sm font-medium text-gray-100">{t("default_video_model")}</div>
        {videoBackends.length > 0 ? (
          <ProviderModelSelect
            value={currentVideo}
            options={videoBackends}
            providerNames={allProviderNames}
            onChange={(v) => setDraft((prev) => ({ ...prev, default_video_backend: v }))}
            allowDefault
            defaultLabel={t("auto_select")}
            defaultHint={t("auto")}
          />
        ) : (
          <div className="rounded-lg border border-gray-800 bg-gray-900/60 px-3 py-2 text-sm text-gray-500">
            {t("no_video_providers_hint")}
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
          {t("generate_audio")}
          <span className="text-xs text-gray-500">{t("audio_support_hint")}</span>
        </label>
      </div>

      {/* Image backend selector */}
      <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
        <div className="mb-3 text-sm font-medium text-gray-100">{t("default_image_model")}</div>
        {imageBackends.length > 0 ? (
          <ProviderModelSelect
            value={currentImage}
            options={imageBackends}
            providerNames={allProviderNames}
            onChange={(v) => setDraft((prev) => ({ ...prev, default_image_backend: v }))}
            allowDefault
            defaultLabel={t("auto_select")}
            defaultHint={t("auto")}
          />
        ) : (
          <div className="rounded-lg border border-gray-800 bg-gray-900/60 px-3 py-2 text-sm text-gray-500">
            {t("no_image_providers_hint")}
          </div>
        )}
      </div>

      {/* Text backend selectors */}
      <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
        <div className="mb-3 text-sm font-medium text-gray-100">{t("text_models")}</div>
        <p className="mb-3 text-xs text-gray-500">{t("text_models_desc")}</p>

        {textBackends.length > 0 ? (
          <div className="space-y-3">
            {TEXT_MODEL_FIELDS.map(([key, label]) => (
              <div key={key}>
                <div className="mb-1 text-xs text-gray-400">{label}</div>
                <ProviderModelSelect
                  value={(draft[key] ?? settings[key] ?? "")}
                  options={textBackends}
                  providerNames={allProviderNames}
                  onChange={(v) => setDraft((prev) => ({ ...prev, [key]: v }))}
                  allowDefault
                  defaultHint={t("auto")}
                  aria-label={label}
                />
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded-lg border border-gray-800 bg-gray-900/60 px-3 py-2 text-sm text-gray-500">
            {t("no_text_providers_hint")}
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
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50 focus-ring"
          >
            {saving ? t("common:saving") : t("common:save")}
          </button>
          <button
            type="button"
            onClick={() => setDraft({})}
            className="rounded-lg border border-gray-700 px-4 py-2 text-sm text-gray-300 transition-colors hover:bg-gray-800 focus-ring"
          >
            {t("common:reset")}
          </button>
        </div>
      )}
    </div>
  );
}
