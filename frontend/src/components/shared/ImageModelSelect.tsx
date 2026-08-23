import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { ProviderModelSelect } from "@/components/ui/ProviderModelSelect";
import { useCapabilitiesStore } from "@/stores/capabilities-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { GetSystemConfigResponse, ImageModelSelection, ModelCandidatesResponse } from "@/types";

interface ImageModelCatalog {
  candidates: ModelCandidatesResponse;
  config: GetSystemConfigResponse;
}

let catalog: ImageModelCatalog | null = null;
let catalogPromise: Promise<ImageModelCatalog> | null = null;
let catalogRevision = -1;

function loadCatalog(revision: number): Promise<ImageModelCatalog> {
  if (catalogRevision !== revision) {
    catalogRevision = revision;
    catalog = null;
    catalogPromise = null;
  }
  if (catalog) return Promise.resolve(catalog);
  const requestedRevision = revision;
  catalogPromise ??= Promise.all([API.getModelCandidates(), API.getSystemConfig()])
    .then(([candidates, config]) => {
      const next = { candidates, config };
      if (catalogRevision === requestedRevision) catalog = next;
      return next;
    })
    .catch((error) => {
      if (catalogRevision === requestedRevision) catalogPromise = null;
      throw error;
    });
  return catalogPromise;
}

export function imageSelectionFromValue(value: string): ImageModelSelection {
  if (!value) return {};
  const slash = value.indexOf("/");
  if (slash <= 0 || slash === value.length - 1) return {};
  return { imageProvider: value.slice(0, slash), imageModel: value.slice(slash + 1) };
}

interface ImageModelSelectProps {
  value: string;
  onChange: (value: string) => void;
  capability?: "t2i" | "i2i" | "any";
  className?: string;
}

/** Request-local selector; it never mutates the project or global defaults. */
export function ImageModelSelect({
  value,
  onChange,
  capability = "any",
  className,
}: ImageModelSelectProps) {
  const { t } = useTranslation("dashboard");
  const [loaded, setLoaded] = useState<ImageModelCatalog | null>(() => catalog);
  const project = useProjectsStore((state) => state.currentProjectData);
  const capabilitiesRevision = useCapabilitiesStore((state) => state.revision);

  useEffect(() => {
    let active = true;
    void loadCatalog(capabilitiesRevision).then((next) => {
      if (active) setLoaded(next);
    }).catch(() => {});
    return () => {
      active = false;
    };
  }, [capabilitiesRevision]);

  const options = useMemo(() => {
    if (!loaded) return [];
    const image = loaded.candidates.image;
    if (capability === "t2i") return image.buckets.t2i ?? image.default;
    if (capability === "i2i") return image.buckets.i2i ?? image.default;
    return Array.from(
      new Set([
        ...(image.buckets.t2i ?? image.default),
        ...(image.buckets.i2i ?? image.default),
      ]),
    );
  }, [capability, loaded]);

  const providerNames = useMemo(
    () => ({
      ...(loaded?.config.options.provider_names ?? {}),
      ...(loaded?.candidates.provider_names ?? {}),
    }),
    [loaded],
  );
  const fallbackValue =
    project?.image_backend ||
    project?.default_image_backend ||
    loaded?.config.settings.default_image_backend ||
    "";

  return (
    <ProviderModelSelect
      value={value}
      options={options}
      providerNames={providerNames}
      onChange={onChange}
      allowDefault
      defaultLabel={t("image_model_project_default")}
      fallbackValue={fallbackValue}
      fallbackLabel={t("image_model_project_default")}
      aria-label={t("image_model_label")}
      className={className}
    />
  );
}
