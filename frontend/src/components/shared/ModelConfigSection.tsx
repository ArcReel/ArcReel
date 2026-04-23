import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { ProviderModelSelect } from "@/components/ui/ProviderModelSelect";
import { DEFAULT_DURATIONS, lookupSupportedDurations, lookupResolutions } from "@/utils/provider-models";
import { ResolutionPicker } from "./ResolutionPicker";
import type { ProviderInfo } from "@/types/provider";
import type { CustomProviderInfo } from "@/types/custom-provider";

// ---------------------------------------------------------------------------
// Module-level stable defaults
// ---------------------------------------------------------------------------

const EMPTY_CUSTOM_PROVIDERS: CustomProviderInfo[] = [];

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export interface ModelConfigValue {
  videoBackend: string; // "" = use global default
  imageBackend: string;
  textBackendScript: string;
  textBackendOverview: string;
  textBackendStyle: string;
  defaultDuration: number | null; // null = auto
  videoResolution: string | null;   // null = use backend default
  imageResolution: string | null;   // null = use backend default
}

export interface ModelConfigSectionProps {
  value: ModelConfigValue;
  onChange: (next: ModelConfigValue) => void;
  /** Backend lists for each dropdown — strings like "gemini-aistudio/veo-3.1-generate-001" */
  options: {
    videoBackends: string[];
    imageBackends: string[];
    textBackends: string[];
    providerNames: Record<string, string>;
  };
  /** For lookupSupportedDurations — providers may be empty if caller hasn't loaded them */
  providers: ProviderInfo[];
  customProviders?: CustomProviderInfo[];
  /** Global default values shown as hint text under each "use global default" option */
  globalDefaults: {
    video: string;
    image: string;
    textScript: string;
    textOverview: string;
    textStyle: string;
  };
  /** Optional visibility toggles (all default true) */
  enable?: {
    video?: boolean;
    image?: boolean;
    text?: boolean;
    duration?: boolean;
  };
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ModelConfigSection({
  value,
  onChange,
  options,
  providers,
  customProviders = EMPTY_CUSTOM_PROVIDERS,
  globalDefaults,
  enable,
}: ModelConfigSectionProps) {
  const { t } = useTranslation("templates");

  const showVideo = enable?.video !== false;
  const showImage = enable?.image !== false;
  const showText = enable?.text !== false;
  const showDuration = enable?.duration !== false;

  // "Follow global default" (empty videoBackend) means the effective backend at
  // generation time will be globalDefaults.video — duration options should
  // reflect that model's real supported_durations, not the generic fallback.
  const effectiveVideoBackend = value.videoBackend || globalDefaults.video || "";

  // Compute supported durations based on current effective video backend (pre-sorted)
  const supportedDurations = useMemo<readonly number[]>(() => {
    const raw = !effectiveVideoBackend
      ? DEFAULT_DURATIONS
      : (lookupSupportedDurations(providers, effectiveVideoBackend, customProviders) ?? DEFAULT_DURATIONS);
    return [...raw].sort((a, b) => a - b);
  }, [providers, effectiveVideoBackend, customProviders]);

  // Video backend change: may reset duration if not supported by new backend
  const handleVideoChange = (next: string) => {
    const effectiveNext = next || globalDefaults.video || "";
    const nextDurations = effectiveNext
      ? (lookupSupportedDurations(providers, effectiveNext, customProviders) ?? DEFAULT_DURATIONS)
      : DEFAULT_DURATIONS;
    const shouldReset =
      value.defaultDuration !== null && !nextDurations.includes(value.defaultDuration);
    onChange({
      ...value,
      videoBackend: next,
      defaultDuration: shouldReset ? null : value.defaultDuration,
      videoResolution: null, // 切换 backend 时清空 resolution，避免残留无效值
    });
  };

  const handleDurationClick = (d: number | null) => {
    onChange({ ...value, defaultDuration: d });
  };

  const renderResolutionField = (backend: string, resolution: string | null, onResolutionChange: (v: string | null) => void) => {
    const res = lookupResolutions(providers, backend, customProviders);
    if (res.options.length === 0) return null;
    return (
      <div className="mt-3 flex items-center gap-2">
        <span className="text-xs text-gray-400">{t("resolution_label")}</span>
        <ResolutionPicker
          mode={res.isCustom ? "combobox" : "select"}
          options={res.options}
          value={resolution}
          onChange={onResolutionChange}
          placeholder={t("resolution_default_placeholder")}
          aria-label={t("resolution_label")}
        />
      </div>
    );
  };

  return (
    <div className="space-y-4">
      {/* Description */}
      <p className="text-sm text-gray-400">{t("default_hint")}</p>

      {/* Video card */}
      {showVideo && (
        <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
          <div className="mb-3 text-sm font-medium text-gray-100">{t("model_video")}</div>
          <ProviderModelSelect
            value={value.videoBackend}
            options={options.videoBackends}
            providerNames={options.providerNames}
            onChange={handleVideoChange}
            allowDefault
            defaultLabel={t("use_global_default")}
            defaultHint={
              globalDefaults.video
                ? t("current_global_default", { value: globalDefaults.video })
                : undefined
            }
            fallbackValue={globalDefaults.video || undefined}
            aria-label={t("model_video")}
          />

          {renderResolutionField(effectiveVideoBackend, value.videoResolution, (v) =>
            onChange({ ...value, videoResolution: v }),
          )}

          {/* Duration picker (nested inside video card) */}
          {showDuration && (
            <>
              <div className="mt-3 mb-2 text-xs text-gray-400">{t("duration_label")}</div>
              <div className="flex flex-wrap gap-2" role="radiogroup" aria-label={t("duration_label")}>
                {/* Auto button */}
                <button
                  type="button"
                  role="radio"
                  aria-checked={value.defaultDuration === null}
                  aria-label={t("duration_auto")}
                  tabIndex={value.defaultDuration === null ? 0 : -1}
                  onClick={() => handleDurationClick(null)}
                  className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
                    value.defaultDuration === null
                      ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
                      : "border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600"
                  }`}
                >
                  {t("duration_auto")}
                </button>

                {/* Per-duration buttons */}
                {supportedDurations.map((d) => (
                  <button
                    key={d}
                    type="button"
                    role="radio"
                    aria-checked={value.defaultDuration === d}
                    aria-label={`${d}s`}
                    tabIndex={value.defaultDuration === d ? 0 : -1}
                    onClick={() => handleDurationClick(d)}
                    className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
                      value.defaultDuration === d
                        ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
                        : "border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600"
                    }`}
                  >
                    {d}s
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {/* Image card */}
      {showImage && (
        <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
          <div className="mb-3 text-sm font-medium text-gray-100">{t("model_image")}</div>
          <ProviderModelSelect
            value={value.imageBackend}
            options={options.imageBackends}
            providerNames={options.providerNames}
            onChange={(next) => onChange({ ...value, imageBackend: next, imageResolution: null })}
            allowDefault
            defaultLabel={t("use_global_default")}
            defaultHint={
              globalDefaults.image
                ? t("current_global_default", { value: globalDefaults.image })
                : undefined
            }
            fallbackValue={globalDefaults.image || undefined}
            aria-label={t("model_image")}
          />

          {renderResolutionField(
            value.imageBackend || globalDefaults.image || "",
            value.imageResolution,
            (v) => onChange({ ...value, imageResolution: v }),
          )}
        </div>
      )}

      {/* Text card */}
      {showText && (
        <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
          <div className="space-y-3">
            {/* Script */}
            <div>
              <div className="mb-1 text-xs text-gray-400">{t("model_text_script")}</div>
              <ProviderModelSelect
                value={value.textBackendScript}
                options={options.textBackends}
                providerNames={options.providerNames}
                onChange={(next) => onChange({ ...value, textBackendScript: next })}
                allowDefault
                defaultLabel={t("use_global_default")}
                defaultHint={
                  globalDefaults.textScript
                    ? t("current_global_default", { value: globalDefaults.textScript })
                    : undefined
                }
                fallbackValue={globalDefaults.textScript || undefined}
                aria-label={t("model_text_script")}
              />
            </div>

            {/* Overview */}
            <div>
              <div className="mb-1 text-xs text-gray-400">{t("model_text_overview")}</div>
              <ProviderModelSelect
                value={value.textBackendOverview}
                options={options.textBackends}
                providerNames={options.providerNames}
                onChange={(next) => onChange({ ...value, textBackendOverview: next })}
                allowDefault
                defaultLabel={t("use_global_default")}
                defaultHint={
                  globalDefaults.textOverview
                    ? t("current_global_default", { value: globalDefaults.textOverview })
                    : undefined
                }
                fallbackValue={globalDefaults.textOverview || undefined}
                aria-label={t("model_text_overview")}
              />
            </div>

            {/* Style */}
            <div>
              <div className="mb-1 text-xs text-gray-400">{t("model_text_style")}</div>
              <ProviderModelSelect
                value={value.textBackendStyle}
                options={options.textBackends}
                providerNames={options.providerNames}
                onChange={(next) => onChange({ ...value, textBackendStyle: next })}
                allowDefault
                defaultLabel={t("use_global_default")}
                defaultHint={
                  globalDefaults.textStyle
                    ? t("current_global_default", { value: globalDefaults.textStyle })
                    : undefined
                }
                fallbackValue={globalDefaults.textStyle || undefined}
                aria-label={t("model_text_style")}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
