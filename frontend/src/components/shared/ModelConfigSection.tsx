import { useEffect, useMemo, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { ProviderModelSelect } from "@/components/ui/ProviderModelSelect";
import { lookupSupportedDurations, lookupResolutions } from "@/utils/provider-models";
import { isContinuousIntegerRange } from "@/utils/duration_format";
import { ResolutionPicker } from "./ResolutionPicker";
import { ImageModelDualSelect } from "./ImageModelDualSelect";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import { CARD_STYLE } from "@/components/ui/darkroom-tokens";
import type { ProviderInfo } from "@/types/provider";
import type { CustomProviderInfo } from "@/types/custom-provider";

const EMPTY_CUSTOM_PROVIDERS: CustomProviderInfo[] = [];

export interface ModelConfigValue {
  videoBackend: string;
  imageBackendT2I: string;
  imageBackendI2I: string;
  textBackendScript: string;
  textBackendOverview: string;
  textBackendStyle: string;
  defaultDuration: number | null;
  videoResolution: string | null;
  imageResolution: string | null;
}

export interface ModelConfigSectionProps {
  value: ModelConfigValue;
  onChange: (next: ModelConfigValue) => void;
  options: {
    videoBackends: string[];
    imageBackends: string[];
    textBackends: string[];
    providerNames: Record<string, string>;
  };
  providers: ProviderInfo[];
  customProviders?: CustomProviderInfo[];
  globalDefaults: {
    video: string;
    imageT2I: string;
    imageI2I: string;
    textScript: string;
    textOverview: string;
    textStyle: string;
  };
  enable?: {
    video?: boolean;
    image?: boolean;
    text?: boolean;
    duration?: boolean;
  };
}

interface ChannelCardProps {
  kicker: string;
  title: string;
  children: React.ReactNode;
}

function ChannelCard({ kicker, title, children }: ChannelCardProps) {
  return (
    <div className="rounded-[10px] border border-hairline p-4" style={CARD_STYLE}>
      <div className="mb-3">
        <div className="font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-accent-2">
          {kicker}
        </div>
        <div className="mt-1 text-[13.5px] font-medium text-text">{title}</div>
      </div>
      {children}
    </div>
  );
}

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

  const endpointToMediaType = useEndpointCatalogStore((s) => s.endpointToMediaType);
  const fetchEndpointCatalog = useEndpointCatalogStore((s) => s.fetch);
  useEffect(() => {
    if (customProviders.length > 0) void fetchEndpointCatalog();
  }, [customProviders.length, fetchEndpointCatalog]);

  const showVideo = enable?.video !== false;
  const showImage = enable?.image !== false;
  const showText = enable?.text !== false;
  const showDuration = enable?.duration !== false;

  const effectiveVideoBackend = value.videoBackend || globalDefaults.video || "";

  const supportedDurations = useMemo<readonly number[] | null>(() => {
    if (!effectiveVideoBackend) return null;
    const raw = lookupSupportedDurations(providers, effectiveVideoBackend, customProviders);
    if (!raw || raw.length === 0) return null;
    return [...raw].sort((a, b) => a - b);
  }, [providers, effectiveVideoBackend, customProviders]);

  const handleVideoChange = (next: string) => {
    const effectiveNext = next || globalDefaults.video || "";
    const nextDurations = effectiveNext
      ? lookupSupportedDurations(providers, effectiveNext, customProviders) ?? null
      : null;
    const shouldReset =
      value.defaultDuration !== null &&
      (!nextDurations || !nextDurations.includes(value.defaultDuration));
    onChange({
      ...value,
      videoBackend: next,
      defaultDuration: shouldReset ? null : value.defaultDuration,
      videoResolution: null,
    });
  };

  const handleDurationClick = (d: number | null) => {
    onChange({ ...value, defaultDuration: d });
  };

  const renderResolutionField = (
    backend: string,
    resolution: string | null,
    onResolutionChange: (v: string | null) => void,
  ) => {
    const res = lookupResolutions(providers, backend, customProviders, endpointToMediaType);
    if (res.options.length === 0) return null;
    return (
      <div className="mt-3 flex items-center gap-2">
        <span className="font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4">
          {t("resolution_label")}
        </span>
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
      <p className="text-[12.5px] leading-[1.55] text-text-3">{t("default_hint")}</p>

      {showVideo && (
        <ChannelCard kicker="Video Channel" title={t("model_video")}>
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

          {showDuration && supportedDurations && supportedDurations.length > 0 && (
            <>
              <div className="mb-2 mt-3 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4">
                {t("duration_label")}
              </div>
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
        </ChannelCard>
      )}

      {showImage && (
        <ChannelCard kicker="Image Channel" title={t("model_image")}>
          <ImageModelDualSelect
            valueT2I={value.imageBackendT2I}
            valueI2I={value.imageBackendI2I}
            options={options.imageBackends}
            providerNames={options.providerNames}
            customProviders={customProviders}
            onChange={({ t2i, i2i }) => {
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
            value.imageBackendT2I || globalDefaults.imageT2I || "",
            value.imageResolution,
            (v) => onChange({ ...value, imageResolution: v }),
          )}
        </ChannelCard>
      )}

      {showText && (
        <ChannelCard kicker="Text Channel" title={t("model_text_script")}>
          <div className="space-y-3.5">
            {/* Script */}
            <div>
              <div className="mb-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4">
                {t("model_text_script")}
              </div>
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
              <div className="mb-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4">
                {t("model_text_overview")}
              </div>
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
              <div className="mb-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4">
                {t("model_text_style")}
              </div>
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
        </ChannelCard>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Duration sub-components
// ---------------------------------------------------------------------------

const DURATION_PILL_BASE =
  "rounded-[7px] border px-3 py-1.5 font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";

const durationActiveCls =
  "border-accent/45 bg-accent-dim text-accent-2";

const durationInactiveCls =
  "border-hairline-soft bg-bg-grad-a/55 text-text-3 hover:border-hairline hover:text-text";

const durationActiveStyle: CSSProperties = {
  boxShadow: "0 0 18px -8px var(--color-accent-glow)",
};

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
  const { t } = useTranslation("dashboard");
  const isAutoActive = value === null;
  return (
    <div className="flex flex-wrap gap-2" role="radiogroup" aria-label={ariaLabel}>
      <button
        type="button"
        role="radio"
        aria-checked={isAutoActive}
        aria-label={autoLabel}
        tabIndex={isAutoActive ? 0 : -1}
        onClick={() => onChange(null)}
        className={`${DURATION_PILL_BASE} ${isAutoActive ? durationActiveCls : durationInactiveCls}`}
        style={isAutoActive ? durationActiveStyle : undefined}
      >
        {autoLabel}
      </button>
      {options.map((d) => {
        const active = value === d;
        return (
          <button
            key={d}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={t("duration_seconds_value_text", { value: d })}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(d)}
            className={`${DURATION_PILL_BASE} ${active ? durationActiveCls : durationInactiveCls}`}
            style={active ? durationActiveStyle : undefined}
          >
            {t("duration_seconds_value_text", { value: d })}
          </button>
        );
      })}
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
  const { t } = useTranslation("dashboard");
  const min = options[0];
  const max = options[options.length - 1];
  const sliderValue = value === null ? min : value;
  const isAutoActive = value === null;
  return (
    <div className="flex flex-wrap items-center gap-3">
      <button
        type="button"
        role="radio"
        aria-checked={isAutoActive}
        aria-label={autoLabel}
        onClick={() => onChange(null)}
        className={`${DURATION_PILL_BASE} ${isAutoActive ? durationActiveCls : durationInactiveCls}`}
        style={isAutoActive ? durationActiveStyle : undefined}
      >
        {autoLabel}
      </button>
      <input
        type="range"
        aria-label={ariaLabel}
        aria-valuetext={
          value === null ? autoLabel : t("duration_seconds_value_text", { value })
        }
        min={min}
        max={max}
        step={1}
        value={sliderValue}
        onChange={(e) => onChange(parseInt(e.target.value, 10))}
        className="min-w-[120px] flex-1 accent-[var(--color-accent)]"
      />
      <span className="min-w-[2.5rem] text-right font-mono text-[11px] tabular-nums text-text-2">
        {value === null ? autoLabel : t("duration_seconds_value_text", { value })}
      </span>
    </div>
  );
}
