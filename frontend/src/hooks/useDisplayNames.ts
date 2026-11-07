import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { catalogDisplayNames, type CatalogDisplayNames } from "@/utils/provider-models";
import type { ModelCandidatesResponse, SystemConfigOptions } from "@/types/system";
import type { CustomProviderInfo } from "@/types/custom-provider";
import type { ProviderInfo } from "@/types/provider";

/** Catalog-name snapshot paired with its language; mismatched-language snapshots never overlay. */
interface LocalizedSnapshot {
  language: string;
  names: CatalogDisplayNames;
}

/**
 * Dropdown provider/model names, four overlays from oldest to newest: catalogs (`/providers`, including unready providers,
 * covering still-effective entries such as built-ins with lost credentials and disabled custom models) → built-in catalogs
 * refetched after language switches → options (page-wide configuration, refetched after saves) → candidates (refetched with language).
 *
 * Language switches never refetch page-wide configuration, which would clear unsaved form edits. Candidates refetch with language but list ready
 * providers only; effective providers with lost credentials can get names only from catalogs. Caller catalogs load alongside forms,
 * independent of language. This hook therefore refetches catalog translations beneath candidates: names only, without rewriting
 * caller catalogs. Capability fields are language-independent, so refetching adds no value and disturbs dependent downstream calculations.
 *
 * Fetch built-in catalogs only: custom provider/model names are user-entered, language-independent, final in the bottom layer.
 *
 * Never fetch or overlay self-fetched snapshots for mount languages: caller catalogs already have that language, so duplicate requests add nothing. Snapshots record
 * their originating language and naturally drop out when returning to mount languages, never covering caller catalogs with previous-language names.
 */
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
      // Retain prior snapshots on failure: old-language names are preferable to bare provider IDs.
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
