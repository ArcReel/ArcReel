import { useTranslation } from "react-i18next";
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
    image: string;
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
    <div className="space-y-4">
      {loading && (
        <div className="text-sm text-gray-500 py-8 text-center">
          {t("common:loading")}
        </div>
      )}
      {error && (
        <div className="text-sm text-red-400 py-8 text-center">{error}</div>
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

      <div className="flex items-center justify-between mt-6 pt-4 border-t border-gray-800">
        <button
          type="button"
          onClick={onCancel}
          className="px-3 py-2 text-sm text-gray-400 hover:text-gray-200"
        >
          {t("common:cancel")}
        </button>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onBack}
            className="rounded-lg border border-gray-700 px-4 py-2 text-sm text-gray-300 hover:bg-gray-800 transition-colors"
          >
            {t("templates:prev_step")}
          </button>
          <button
            type="button"
            onClick={onNext}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 transition-colors disabled:opacity-50"
            disabled={loading}
          >
            {t("templates:next_step")}
          </button>
        </div>
      </div>
    </div>
  );
}
