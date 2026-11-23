import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { catalogDisplayNames, type CatalogDisplayNames } from "@/utils/provider-models";
import type { ModelCandidatesResponse, SystemConfigOptions } from "@/types/system";
import type { CustomProviderInfo } from "@/types/custom-provider";
import type { ProviderInfo } from "@/types/provider";

interface LocalizedSnapshot {
  language: string;
  names: CatalogDisplayNames;
}

export function useDisplayNames(
  providers: ProviderInfo[],
  customProviders: CustomProviderInfo[],
  options: Pick<SystemConfigOptions, "provider_names" | "model_names"> | null,
  candidates: ModelCandidatesResponse | null,
): CatalogDisplayNames {
  const { i18n } = useTranslation();
  const language = i18n.language;
  const [snapshot, setSnapshot] = useState<LocalizedSnapshot | null>(null);
  const mountLanguageRef = useRef(language);

  useEffect(() => {
    if (language === mountLanguageRef.current) return;
    const controller = new AbortController();
    void API.getProviders({ signal: controller.signal }).then(
      (res) => {
        if (controller.signal.aborted) return;
         
        setSnapshot({ language, names: catalogDisplayNames(res.providers) });
      },
      () => undefined,
    );
    return () => controller.abort();
  }, [language]);

  const catalog = useMemo(
    () => catalogDisplayNames(providers, customProviders),
    [providers, customProviders],
  );
  const localized = snapshot?.language === language ? snapshot.names : null;
  return useMemo(
    () => ({
      providerNames: {
        ...catalog.providerNames,
        ...(localized?.providerNames ?? {}),
        ...(options?.provider_names ?? {}),
        ...(candidates?.provider_names ?? {}),
      },
      modelNames: {
        ...catalog.modelNames,
        ...(localized?.modelNames ?? {}),
        ...(options?.model_names ?? {}),
        ...(candidates?.model_names ?? {}),
      },
    }),
    [catalog, localized, options, candidates],
  );
}
