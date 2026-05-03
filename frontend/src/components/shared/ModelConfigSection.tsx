import { useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { ProviderModelSelect } from "@/components/ui/ProviderModelSelect";
import { lookupSupportedDurations, lookupResolutions } from "@/utils/provider-models";
import { isContinuousIntegerRange } from "@/utils/duration_format";
import { ResolutionPicker } from "./ResolutionPicker";
import { ImageModelDualSelect } from "./ImageModelDualSelect";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
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
  imageBackendT2I: string; // "" = use global default (T2I slot)
  imageBackendI2I: string; // "" = use global default (I2I slot)
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
    imageT2I: string;
    imageI2I: string;
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

  // 自定义 provider 的 resolution picker 需要从 endpoint 推 mediaType；catalog 即时拉取。
  const endpointToMediaType = useEndpointCatalogStore((s) => s.endpointToMediaType);
  const fetchEndpointCatalog = useEndpointCatalogStore((s) => s.fetch);
  useEffect(() => {
    if (customProviders.length > 0) void fetchEndpointCatalog();
  }, [customProviders.length, fetchEndpointCatalog]);

  const showVideo = enable?.video !== false;
  const showImage = enable?.image !== false;
  const showText = enable?.text !== false;
  const showDuration = enable?.duration !== false;

  // "Follow global default" (empty videoBackend) means the effective backend at
  // generation time will be globalDefaults.video — duration options should
  // reflect that model's real supported_durations, not the generic fallback.
  const effectiveVideoBackend = value.videoBackend || globalDefaults.video || "";

  // 找不到 supported_durations 时返回 null（隐藏整个时长卡片，不再用 [4,6,8] 兜底）
  const supportedDurations = useMemo<readonly number[] | null>(() => {
    if (!effectiveVideoBackend) return null;
    const raw = lookupSupportedDurations(providers, effectiveVideoBackend, customProviders);
    if (!raw || raw.length === 0) return null;
    return [...raw].sort((a, b) => a - b);
  }, [providers, effectiveVideoBackend, customProviders]);

  // Video backend change: may reset duration if not supported by new backend
  const handleVideoChange = (next: string) => {
    const effectiveNext = next || globalDefaults.video || "";
    const nextDurations = effectiveNext
      ? (lookupSupportedDurations(providers, effectiveNext, customProviders) ?? null)
      : null;
    const shouldReset =
      value.defaultDuration !== null && (!nextDurations || !nextDurations.includes(value.defaultDuration));
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
    const res = lookupResolutions(providers, backend, customProviders, endpointToMediaType);
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

          {/* Duration picker — 找不到 supported_durations 时不渲染 */}
          {showDuration && supportedDurations && supportedDurations.length > 0 && (
            <>
              <div className="mt-3 mb-2 text-xs text-gray-400">{t("duration_label")}</div>
              {isContinuousIntegerRange(supportedDurations) && supportedDurations.length >= 5 ? (
                <DurationSlider
                  options={supportedDurations}
                  value={value.defaultDuration}
                  onChange={handleDurationClick}
                  ariaLabel={t("duration_label")}
                  autoLabel={t("duration_auto")}
                />
              ) : (
                <DurationButtonGroup
                  options={supportedDurations}
                  value={value.defaultDuration}
                  onChange={handleDurationClick}
                  ariaLabel={t("duration_label")}
                  autoLabel={t("duration_auto")}
                />
              )}
            </>
          )}
        </div>
      )}

      {/* Image card */}
      {showImage && (
        <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
          <div className="mb-3 text-sm font-medium text-gray-100">{t("model_image")}</div>
          <ImageModelDualSelect
            valueT2I={value.imageBackendT2I}
            valueI2I={value.imageBackendI2I}
            options={options.imageBackends}
            providerNames={options.providerNames}
            customProviders={customProviders}
            onChange={({ t2i, i2i }) => {
              // 分辨率绑定 T2I（canonical slot），仅在 effective T2I 变化时清空
              const prevEffectiveT2I = value.imageBackendT2I || globalDefaults.imageT2I || "";
              const nextEffectiveT2I = t2i || globalDefaults.imageT2I || "";
              const next: ModelConfigValue = {
                ...value,
                imageBackendT2I: t2i,
                imageBackendI2I: i2i,
              };
              if (prevEffectiveT2I !== nextEffectiveT2I) next.imageResolution = null;
              onChange(next);
            }}
            globalDefaultT2I={globalDefaults.imageT2I || undefined}
            globalDefaultI2I={globalDefaults.imageI2I || undefined}
          />

          {renderResolutionField(
            // T2I is treated as the canonical slot for resolution computation
            value.imageBackendT2I || globalDefaults.imageT2I || "",
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

// ---------------------------------------------------------------------------
// Duration sub-components
// ---------------------------------------------------------------------------

function DurationButtonGroup({
  options,
  value,
  onChange,
  ariaLabel,
  autoLabel,
}: {
  options: readonly number[];
  value: number | null;
  onChange: (next: number | null) => void;
  ariaLabel: string;
  autoLabel: string;
}) {
  return (
    <div className="flex flex-wrap gap-2" role="radiogroup" aria-label={ariaLabel}>
      <button
        type="button"
        role="radio"
        aria-checked={value === null}
        aria-label={autoLabel}
        tabIndex={value === null ? 0 : -1}
        onClick={() => onChange(null)}
        className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
          value === null
            ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
            : "border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600"
        }`}
      >
        {autoLabel}
      </button>
      {options.map((d) => (
        <button
          key={d}
          type="button"
          role="radio"
          aria-checked={value === d}
          aria-label={`${d}s`}
          tabIndex={value === d ? 0 : -1}
          onClick={() => onChange(d)}
          className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
            value === d
              ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
              : "border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600"
          }`}
        >
          {d}s
        </button>
      ))}
    </div>
  );
}

function DurationSlider({
  options,
  value,
  onChange,
  ariaLabel,
  autoLabel,
}: {
  options: readonly number[];
  value: number | null;
  onChange: (next: number | null) => void;
  ariaLabel: string;
  autoLabel: string;
}) {
  const min = options[0];
  const max = options[options.length - 1];
  const sliderValue = value === null ? min : value;
  return (
    <div className="flex flex-wrap items-center gap-3">
      <button
        type="button"
        role="radio"
        aria-checked={value === null}
        aria-label={autoLabel}
        onClick={() => onChange(null)}
        className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
          value === null
            ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
            : "border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600"
        }`}
      >
        {autoLabel}
      </button>
      <input
        type="range"
        role="slider"
        aria-label={ariaLabel}
        min={min}
        max={max}
        step={1}
        value={sliderValue}
        onChange={(e) => onChange(parseInt(e.target.value, 10))}
        className="flex-1 min-w-[120px]"
      />
      <span className="min-w-[2.5rem] text-right text-xs text-gray-300">
        {value === null ? autoLabel : `${value}s`}
      </span>
    </div>
  );
}
