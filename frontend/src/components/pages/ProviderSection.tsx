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
import {
  buildPresetRailEntries,
  resolvePresetRailActiveId,
  resolveVendorChannels,
} from "./settings/providerGroups";
import type { ProviderInfo } from "@/types";

type Selection =
  | { kind: "preset"; id: string }
  | { kind: "custom"; id: number }
  | { kind: "new-custom" }
  | null;

/** 二级栏条目的 id：预置与自定义供应商的 id 可能撞值，加上分组前缀。 */
function railId(selection: Selection, providers: readonly ProviderInfo[]): string | null {
  if (!selection) return null;
  if (selection.kind === "preset") return resolvePresetRailActiveId(providers, selection.id);
  if (selection.kind === "custom") return `custom:${selection.id}`;
  return "custom:new";
}

/** 自定义供应商不按名称猜品牌图标：中转站与协议无关，统一用名称首字。 */
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

  // 保存本身已成功，只是目录重取失败：与「保存失败」区分开，否则用户看到表单无错、列表无新项，
  // 分不清是哪一步没成。被后续请求作废（aborted）是正常并发路径，接管方会写下更新的目录。
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
    const firstTarget = buildPresetRailEntries(providers, null)[0]?.targetProviderId;
    if (firstTarget) navigate(providerSettingsPath({ preset: firstTarget }), { replace: true });
  }, [providers, navigate]);

  // 从「调用端点」小节的「新建供应商并使用此端点」接线过来的预填。
  const prefill = useMemo(() => {
    const params = new URLSearchParams(search);
    return {
      baseUrl: params.get("base_url") ?? undefined,
      endpoint: params.get("endpoint") ?? undefined,
    };
  }, [search]);

  useEffect(() => {
    if (loading || selection) return;
    selectFirstPreset();
  }, [loading, selection, selectFirstPreset]);

  const activePresetId = selection?.kind === "preset" ? selection.id : null;
  const activeVendorChannels = useMemo(
    () => (activePresetId ? resolveVendorChannels(providers, activePresetId) : undefined),
    [providers, activePresetId],
  );
  const selectPresetChannel = useCallback(
    (nextProviderId: string) => {
      navigate(providerSettingsPath({ preset: nextProviderId }), { replace: true });
    },
    [navigate],
  );

  const railGroups = useMemo<SecondaryRailGroup[]>(
    () => [
      {
        id: "preset",
        label: t("provider_rail_preset"),
        items: buildPresetRailEntries(providers, activePresetId).map((entry) => ({
          id: entry.railItemId,
          label: entry.labelKey !== undefined ? t(entry.labelKey) : entry.displayName,
          description:
            entry.credentialCount > 0
              ? t("provider_credential_count", { count: entry.credentialCount })
              : t("status_unconfigured"),
          icon: <ProviderIcon providerId={entry.iconProviderId} className="size-4" />,
          href: providerSettingsPath({ preset: entry.targetProviderId }),
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
    [t, providers, activePresetId, customProviders],
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
    <div className="flex min-h-0 min-w-0 flex-1">
      <SecondaryRail label={t("provider_list")} groups={railGroups} activeId={railId(selection, providers)} replace />

      {selection?.kind === "preset" && (
        // 换供应商时整栏重建：未保存的高级配置、在途保存与加载状态都属于上一个供应商
        // 深链换了要打开的模型时同样重建
        <ProviderDetail
          key={`${selection.id}:${modelId ?? ""}`}
          providerId={selection.id}
          initialModelId={modelId}
          channels={activeVendorChannels}
          onSelectChannel={selectPresetChannel}
          onSaved={refreshAfterSave}
        />
      )}
      {selection?.kind === "custom" && (
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
          // 预填参数变了（从另一个端点接线过来）就是另一张新建表单
          key={`new:${prefill.endpoint ?? ""}:${prefill.baseUrl ?? ""}`}
          initialBaseUrl={prefill.baseUrl}
          initialEndpoint={prefill.endpoint}
          onSaved={(created) => {
            // 选中用新建响应带回的 id，不等目录重取的结局：重取被后续请求接管时，
            // 用户会留在填满的新建表单上，再保存一次就多出一个重复供应商。
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
