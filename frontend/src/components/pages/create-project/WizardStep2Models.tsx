import type { CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { ModelConfigSection, type ModelConfigValue } from "@/components/shared/ModelConfigSection";
import type { ProviderInfo } from "@/types";
import type { CustomProviderInfo } from "@/types/custom-provider";

export interface WizardStep2Data {
  options: {
    video: string[];
    image: string[];
    text: string[];
    providerNames: Record<string, string>;
  };
  providers: ProviderInfo[];
  customProviders: CustomProviderInfo[];
  globalDefaults: {
    video: string;
    imageT2I: string;
    imageI2I: string;
    textScript: string;
    textOverview: string;
    textStyle: string;
  };
}

export interface WizardStep2ModelsProps {
  value: ModelConfigValue;
  onChange: (next: ModelConfigValue) => void;
  onBack: () => void;
  onNext: () => void;
  onCancel: () => void;
  data: WizardStep2Data | null;
  error: string | null;
}

const ACCENT_BUTTON_STYLE: CSSProperties = {
  color: "oklch(0.14 0 0)",
  background: "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))",
  boxShadow:
    "inset 0 1px 0 oklch(1 0 0 / 0.3), 0 0 0 1px oklch(0.55 0.10 295 / 0.4), 0 6px 18px -8px var(--color-accent-glow)",
};

export function WizardStep2Models({
  value,
  onChange,
  onBack,
  onNext,
  onCancel,
  data,
  error,
}: WizardStep2ModelsProps) {
  const { t } = useTranslation(["common", "templates"]);
  const loading = !data && !error;

  return (
    <div className="space-y-5">
      {loading && (
        <div className="flex items-center justify-center gap-2 py-12 text-text-3">
          <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin text-accent-2" aria-hidden />
          <span className="font-mono text-[11px] uppercase tracking-[0.14em]">{t("common:loading")}</span>
        </div>
      )}
      {error && (
        <div className="rounded-[8px] border border-hairline-soft bg-bg-grad-a/45 px-4 py-6 text-center">
          <div className="font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-warm">
            ▲ Error
          </div>
          <p className="mt-1.5 text-[12.5px] text-text-2">{error}</p>
        </div>
      )}
      {data && (
        <ModelConfigSection
          value={value}
          onChange={onChange}
          providers={data.providers}
          customProviders={data.customProviders}
          options={{
            videoBackends: data.options.video,
            imageBackends: data.options.image,
            textBackends: data.options.text,
            providerNames: data.options.providerNames,
          }}
          globalDefaults={data.globalDefaults}
        />
      )}

      <div className="mt-7 flex items-center justify-between border-t border-hairline-soft pt-5">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-[7px] px-2.5 py-1.5 text-[12.5px] text-text-3 transition-colors hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          {t("common:cancel")}
        </button>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onBack}
            className="inline-flex items-center gap-1.5 rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3.5 py-2 text-[12.5px] text-text-2 transition-colors hover:border-hairline-strong hover:bg-bg-grad-a hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <span aria-hidden>←</span>
            {t("templates:prev_step")}
          </button>
          <button
            type="button"
            onClick={onNext}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-[8px] px-4 py-2 text-[12.5px] font-semibold transition-transform motion-safe:hover:-translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50"
            style={ACCENT_BUTTON_STYLE}
          >
            {t("templates:next_step")}
            <span aria-hidden>→</span>
          </button>
        </div>
      </div>
    </div>
  );
}
