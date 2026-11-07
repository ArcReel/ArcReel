import { useEffect, useMemo, useCallback } from "react";
import { useLocation, useSearch } from "wouter";
import { Loader2, Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { providerSettingsPath } from "@/app-routes";
import { useProviderCatalog } from "@/hooks/useProviderCatalog";
import type { CatalogRefreshResult } from "@/hooks/useProviderCatalog";
import { useAppStore } from "@/stores/app-store";
import { ProviderIcon } from "@/components/shared/ProviderIcon";
import { DetailPane } from "@/components/shared/master-detail/DetailPane";
import { SecondaryRail, type SecondaryRailGroup } from "@/components/shared/master-detail/SecondaryRail";
import { Button } from "@/components/ui/button";
import { ProviderDetail } from "./ProviderDetail";
import { CustomProviderDetail } from "./settings/CustomProviderDetail";
import { CustomProviderForm } from "./settings/CustomProviderForm";

type Selection =
  | { kind: "preset"; id: string }
  | { kind: "custom"; id: number }
  | { kind: "new-custom" }
  | null;

/** Secondary-sidebar entry IDs: built-in/custom provider IDs may collide, so add group prefixes. */
function railId(selection: Selection): string | null {
  if (!selection) return null;
  if (selection.kind === "preset") return `preset:${selection.id}`;
  if (selection.kind === "custom") return `custom:${selection.id}`;
  return "custom:new";
}

/** Never guess custom-provider brand icons from names; relay sites are protocol-independent, so use name initials uniformly. */
function CustomProviderGlyph({ name }: { name: string }) {
  return (
    <span className="inline-flex size-4 items-center justify-center rounded-sm border border-border bg-muted text-xs leading-none text-subtle-foreground">
      {Array.from(name)[0] ?? "?"}
    </span>
  );
}

export function ProviderSection() {
  const { t, i18n } = useTranslation(["dashboard", "common"]);
  const { providers, customProviders, loading, error: loadError, reload, refresh } = useProviderCatalog(i18n.language);
  const [, navigate] = useLocation();
  const search = useSearch();

  const selection: Selection = useMemo(() => {
    const params = new URLSearchParams(search);
    const preset = params.get("provider");
    const custom = params.get("custom");
    if (custom === "new") return { kind: "new-custom" };
    if (custom) {
      const id = parseInt(custom, 10);
      if (!isNaN(id)) return { kind: "custom", id };
    }
    if (preset) return { kind: "preset", id: preset };
    return null;
  }, [search]);
  const modelId = new URLSearchParams(search).get("model") ?? undefined;

  // Saves succeeded but catalog refetch failed: distinguish from save failures, or valid forms without new list entries leave users
  // unsure which step failed. Later-request abortion is normal concurrency; successors write updated catalogs.
  const pushToast = useAppStore((s) => s.pushToast);
  const notifyRefreshFailure = useCallback(
    (result: CatalogRefreshResult) => {
      if (result.status === "failed") pushToast(t("provider_saved_refresh_failed"), "warning");
    },
    [pushToast, t],
  );
  const refreshAfterSave = useCallback(() => {
    void refresh().then(notifyRefreshFailure);
  }, [refresh, notifyRefreshFailure]);

  const selectFirstPreset = useCallback(() => {
    if (providers.length > 0) navigate(providerSettingsPath({ preset: providers[0].id }), { replace: true });
  }, [providers, navigate]);

  // Prefill wired from New provider using this endpoint in Endpoints sections.
  const prefill = useMemo(() => {
    const params = new URLSearchParams(search);
    return {
      baseUrl: params.get("base_url") ?? undefined,
      endpoint: params.get("endpoint") ?? undefined,
    };
  }, [search]);

  // Select first preset as fallback after fetching if URLs still lack selections.
  useEffect(() => {
    if (loading || selection) return;
    selectFirstPreset();
  }, [loading, selection, selectFirstPreset]);

  const railGroups = useMemo<SecondaryRailGroup[]>(
    () => [
      {
        id: "preset",
        label: t("provider_rail_preset"),
        items: providers.map((p) => ({
          id: `preset:${p.id}`,
          label: p.display_name,
          description:
            p.credential_count > 0
              ? t("provider_credential_count", { count: p.credential_count })
              : t("status_unconfigured"),
          icon: <ProviderIcon providerId={p.id} className="size-4" />,
          href: providerSettingsPath({ preset: p.id }),
        })),
      },
      {
        id: "custom",
        label: t("provider_rail_custom"),
        items: customProviders.map((p) => ({
          id: `custom:${p.id}`,
          label: p.display_name,
          description: t("custom_provider_model_count", { count: p.models.length }),
          icon: <CustomProviderGlyph name={p.display_name} />,
          href: providerSettingsPath({ custom: p.id }),
        })),
        action: {
          id: "custom:new",
          label: t("add_custom_provider"),
          icon: <Plus className="size-4" />,
          href: providerSettingsPath({ newCustom: {} }),
        },
        emptyText: t("custom_providers_empty"),
      },
    ],
    [t, providers, customProviders],
  );

  if (loadError) {
    return (
      <div role="alert" className="flex flex-col items-start gap-3 p-6">
        <p className="text-sm font-medium text-warn">{t("common:load_failed")}</p>
        <p className="text-sm text-subtle-foreground">{loadError}</p>
        <Button variant="outline" size="sm" onClick={reload}>
          {t("common:retry")}
        </Button>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
        {t("loading_providers")}
      </div>
    );
  }

  return (
    // Full-bleed layout: secondary and detail columns scroll independently
    <div className="flex min-h-0 min-w-0 flex-1">
      <SecondaryRail label={t("provider_list")} groups={railGroups} activeId={railId(selection)} replace />

      {selection?.kind === "preset" && (
        // Provider switches rebuild whole sections: unsaved advanced settings/in-flight saves/loading belong to previous providers.
        // Changed deep-linked models also rebuild.
        <ProviderDetail
          key={`${selection.id}:${modelId ?? ""}`}
          providerId={selection.id}
          initialModelId={modelId}
          onSaved={refreshAfterSave}
        />
      )}
      {selection?.kind === "custom" && (
        // Changed deep-linked models also rebuild.
        <CustomProviderDetail
          key={`${selection.id}:${modelId ?? ""}`}
          providerId={selection.id}
          initialModelId={modelId}
          onDeleted={() => {
            void refresh();
            selectFirstPreset();
          }}
          onSaved={refreshAfterSave}
        />
      )}
      {selection?.kind === "new-custom" && (
        <CustomProviderForm
          // Changed prefill from other endpoints means new creation forms.
          key={`new:${prefill.endpoint ?? ""}:${prefill.baseUrl ?? ""}`}
          initialBaseUrl={prefill.baseUrl}
          initialEndpoint={prefill.endpoint}
          onSaved={(created) => {
            // Select IDs from creation responses without awaiting catalog refetches; when successors take over refetches,
            // otherwise users stay on filled creation forms and saving again creates duplicate providers.
            if (created) navigate(providerSettingsPath({ custom: created.id }), { replace: true });
            refreshAfterSave();
          }}
        />
      )}
      {!selection && (
        <DetailPane>
          <p className="p-6 text-sm text-muted-foreground">{t("select_provider")}</p>
        </DetailPane>
      )}
    </div>
  );
}
