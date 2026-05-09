import { useTranslation } from "react-i18next";
import { AlertTriangle, Loader2 } from "lucide-react";
import { ModelConfigSection, type ModelConfigValue } from "@/components/shared/ModelConfigSection";
import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE, GHOST_BTN_LG_CLS } from "@/components/ui/darkroom-tokens";
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
        <div role="alert" className="rounded-[8px] border border-hairline-soft bg-bg-grad-a/45 px-4 py-6 text-center">
          <div className="inline-flex items-center gap-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-warm">
            <AlertTriangle aria-hidden className="h-3 w-3" />
            {t("common:error")}
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
            className={GHOST_BTN_LG_CLS}
          >
            <span aria-hidden>←</span>
            {t("templates:prev_step")}
          </button>
          <button
            type="button"
            onClick={onNext}
            disabled={loading}
            className={ACCENT_BTN_CLS}
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
