import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { ModelConfigSection, type ModelConfigValue } from "@/components/shared/ModelConfigSection";
import { PROVIDER_NAMES } from "@/components/ui/ProviderIcon";
import type { ProviderInfo } from "@/types";
import type { CustomProviderInfo } from "@/types/custom-provider";

export interface WizardStep2ModelsProps {
  value: ModelConfigValue;
  onChange: (next: ModelConfigValue) => void;
  onBack: () => void;
  onNext: () => void;
  onCancel: () => void;
}

export function WizardStep2Models({
  value,
  onChange,
  onBack,
  onNext,
  onCancel,
}: WizardStep2ModelsProps) {
  const { t } = useTranslation(["common", "templates"]);

  const [options, setOptions] = useState<{
    video: string[];
    image: string[];
    text: string[];
    providerNames: Record<string, string>;
  } | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [customProviders, setCustomProviders] = useState<CustomProviderInfo[]>([]);
  const [globalDefaults, setGlobalDefaults] = useState({
    video: "",
    image: "",
    textScript: "",
    textOverview: "",
    textStyle: "",
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [sysConfig, providersRes, customRes] = await Promise.all([
          API.getSystemConfig(),
          API.getProviders(),
          API.listCustomProviders(),
        ]);
        if (cancelled) return;
        setProviders(providersRes.providers);
        setCustomProviders(customRes.providers);
        setOptions({
          video: sysConfig.options.video_backends,
          image: sysConfig.options.image_backends,
          text: sysConfig.options.text_backends,
          providerNames: { ...PROVIDER_NAMES, ...(sysConfig.options.provider_names ?? {}) },
        });
        setGlobalDefaults({
          video: sysConfig.settings.default_video_backend ?? "",
          image: sysConfig.settings.default_image_backend ?? "",
          textScript: sysConfig.settings.text_backend_script ?? "",
          textOverview: sysConfig.settings.text_backend_overview ?? "",
          textStyle: sysConfig.settings.text_backend_style ?? "",
        });
        setLoading(false);
      } catch (err) {
        if (!cancelled) {
          setError((err as Error).message);
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

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
      {!loading && !error && options && (
        <ModelConfigSection
          value={value}
          onChange={onChange}
          providers={providers}
          customProviders={customProviders}
          options={{
            videoBackends: options.video,
            imageBackends: options.image,
            textBackends: options.text,
            providerNames: options.providerNames,
          }}
          globalDefaults={globalDefaults}
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
