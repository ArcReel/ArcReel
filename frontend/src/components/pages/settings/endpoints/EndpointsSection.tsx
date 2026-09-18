import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useSearch } from "wouter";
import { FileJson2, Loader2, Lock, Plus, Store, Upload } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { errMsg, voidCall } from "@/utils/async";
import { useAppStore } from "@/stores/app-store";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import { GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";
import type {
  AnyEndpointDefinition,
  CustomEndpointInfo,
  CustomProviderInfo,
  EndpointDefinition,
  EndpointDescriptor,
  EndpointInstallation,
  EndpointReference,
  EndpointValidateResponse,
  MarketEntry,
} from "@/types";
import { MarketInstallDialog } from "../market/MarketInstallDialog";
import { isDeclarativeDefinition, newEndpointDefinition } from "./endpoint-definition-draft";
import { EndpointDetail, type EndpointSelection } from "./EndpointDetail";
import { EndpointImportDialog } from "./EndpointImportDialog";

const KICKER_CLS = "font-mono text-[9.5px] font-bold uppercase tracking-[0.16em] text-text-4";

/** 有 kind 即是一份此刻就能显示的端点定义；其余形状的身份由服务端的分流结果给出。 */
function hasKind(value: unknown): value is AnyEndpointDefinition {
  return typeof value === "object" && value !== null && "kind" in value;
}

interface ListEntry {
  key: string;
  label: string;
  python: boolean;
  referenceCount: number;
}

interface MarketUpdateTarget {
  entry: MarketEntry;
  currentDefinition: EndpointDefinition;
  hasUnsavedChanges: boolean;
}

/**
 * 调用端点小节：左侧按归属分组的端点列表，右侧生命周期表单。
 * 选中项写进 URL 的 endpoint 参数，刷新与外部跳转都能落回同一个端点。
 */
export function EndpointsSection() {
  const { t } = useTranslation(["dashboard", "common"]);
  const [location, navigate] = useLocation();
  const search = useSearch();
  const pushToast = useAppStore((s) => s.pushToast);

  const catalog = useEndpointCatalogStore((s) => s.endpoints);
  const refreshCatalog = useEndpointCatalogStore((s) => s.refresh);

  const [customEndpoints, setCustomEndpoints] = useState<CustomEndpointInfo[]>([]);
  const [providers, setProviders] = useState<CustomProviderInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [importFileName, setImportFileName] = useState("");
  const [importDefinition, setImportDefinition] = useState<AnyEndpointDefinition | null>(null);
  const [importValidation, setImportValidation] = useState<EndpointValidateResponse | null>(null);
  const [importBusy, setImportBusy] = useState(false);
  const [importOpen, setImportOpen] = useState(false);

  const [marketUpdateTarget, setMarketUpdateTarget] = useState<MarketUpdateTarget | null>(null);
  const [marketUpdatePending, setMarketUpdatePending] = useState(false);
  const marketUpdateRef = useRef<AbortController | null>(null);

  const selectedKey = new URLSearchParams(search).get("endpoint");

  const select = useCallback(
    (key: string | null) => {
      const params = new URLSearchParams(search);
      if (key === null) params.delete("endpoint");
      else params.set("endpoint", key);
      navigate(`${location}?${params.toString()}`, { replace: true });
    },
    [search, location, navigate],
  );

  const reload = useCallback(async () => {
    const [endpointsRes, providersRes] = await Promise.all([
      API.listCustomEndpoints(),
      API.listCustomProviders(),
    ]);
    setCustomEndpoints(endpointsRes.endpoints);
    setProviders(providersRes.providers);
    await refreshCatalog();
  }, [refreshCatalog]);

  useEffect(() => {
    let disposed = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    setLoadError(null);
    voidCall(
      reload()
        .catch((e) => {
          if (!disposed) setLoadError(errMsg(e));
        })
        .finally(() => {
          if (!disposed) setLoading(false);
        }),
    );
    return () => {
      disposed = true;
    };
  }, [reload, reloadKey]);

  /** 模型行对端点的引用数：只有自定义供应商的模型行能引用 ce-* 键。 */
  const referenceCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const provider of providers) {
      for (const model of provider.models) {
        counts[model.endpoint] = (counts[model.endpoint] ?? 0) + 1;
      }
    }
    return counts;
  }, [providers]);

  // 本节管的是自定义端点，内置那两组只作参照。自定义端点的媒体类型由定义自己声明（一份
  // ComfyUI workflow 可以产图），因此自定义端点不按 video 过滤——否则导进来的图像端点在设置页
  // 里既看不到也删不掉。内置端点仍只列视频：内置图像端点在这里没有管理面。
  const sectionCatalog = useMemo(
    () => catalog.filter((endpoint) => endpoint.media_type === "video" || endpoint.source === "custom"),
    [catalog],
  );

  const groups = useMemo(() => {
    const toEntry = (descriptor: EndpointDescriptor): ListEntry => ({
      key: descriptor.key,
      label: descriptor.display_name ?? t(descriptor.display_name_key),
      python: descriptor.kind === "python",
      referenceCount: referenceCounts[descriptor.key] ?? 0,
    });
    return [
      {
        labelKey: "ce_group_mine",
        entries: sectionCatalog.filter((e) => e.source === "custom").map(toEntry),
      },
      {
        labelKey: "ce_group_builtin",
        entries: sectionCatalog
          .filter((e) => e.source === "builtin" && e.kind === "declarative")
          .map(toEntry),
      },
      {
        labelKey: "ce_group_builtin_python",
        entries: sectionCatalog.filter((e) => e.kind === "python").map(toEntry),
      },
    ];
  }, [sectionCatalog, referenceCounts, t]);

  const selection = useMemo((): EndpointSelection | null => {
    if (selectedKey === "new") {
      return { mode: "new", definition: newEndpointDefinition("") };
    }
    const record = customEndpoints.find((e) => e.key === selectedKey);
    if (record) {
      // 详情表单只吃声明式定义；ComfyUI 端点走它自己那一路，否则表单会解引用它没有的 submit / poll。
      return isDeclarativeDefinition(record.definition)
        ? { mode: "custom", record, definition: record.definition }
        : { mode: "comfyui", record };
    }
    const descriptor = sectionCatalog.find((e) => e.key === selectedKey);
    if (!descriptor) return null;
    return descriptor.kind === "python"
      ? { mode: "python", descriptor }
      : { mode: "builtin", descriptor };
  }, [selectedKey, customEndpoints, sectionCatalog]);

  // --- 导入 ---

  // 连续选文件时后一次接管：作废在途的读取与校验，避免第二个文件的定义配上
  // 第一个文件的校验结果。
  const importRunRef = useRef(0);

  const handleFilePicked = useCallback(
    async (file: File) => {
      const run = ++importRunRef.current;
      setImportFileName(file.name);
      setImportValidation(null);
      setImportDefinition(null);
      setImportOpen(true);
      try {
        const parsed: unknown = JSON.parse(await file.text());
        if (importRunRef.current !== run) return;
        // 不带 kind 的载荷此刻还没有定义身份：服务端按 workflow 收下时，包装结果随校验结果回来。
        const picked = hasKind(parsed) ? parsed : null;
        setImportDefinition(picked);
        const result = await API.validateCustomEndpoint(parsed);
        if (importRunRef.current !== run) return;
        setImportDefinition(result.wrapped_definition ?? picked);
        setImportValidation(result);
      } catch (e) {
        if (importRunRef.current !== run) return;
        setImportOpen(false);
        pushToast(errMsg(e, t("ce_import_read_failed")), "error");
      }
    },
    [pushToast, t],
  );

  const finishImport = useCallback(
    async (saved: CustomEndpointInfo) => {
      setImportOpen(false);
      await reload();
      select(saved.key);
    },
    [reload, select],
  );

  const handleImportCreate = useCallback(async () => {
    if (!importDefinition) return;
    setImportBusy(true);
    try {
      await finishImport(await API.createCustomEndpoint(importDefinition));
      pushToast(t("ce_imported"), "success");
    } catch (e) {
      pushToast(errMsg(e, t("ce_import_failed")), "error");
    } finally {
      setImportBusy(false);
    }
  }, [importDefinition, finishImport, pushToast, t]);

  const handleImportOverwrite = useCallback(
    async (id: number) => {
      if (!importDefinition) return;
      setImportBusy(true);
      try {
        await finishImport(await API.updateCustomEndpoint(id, importDefinition));
        pushToast(t("ce_imported"), "success");
      } catch (e) {
        pushToast(errMsg(e, t("ce_import_failed")), "error");
      } finally {
        setImportBusy(false);
      }
    },
    [importDefinition, finishImport, pushToast, t],
  );

  // --- 从市场更新 ---

  useEffect(() => {
    marketUpdateRef.current?.abort();
  }, [selectedKey]);

  useEffect(
    () => () => {
      marketUpdateRef.current?.abort();
      marketUpdateRef.current = null;
    },
    [],
  );

  const handleUpdateFromMarket = useCallback(
    async (
      installation: EndpointInstallation,
      currentDefinition: EndpointDefinition,
      hasUnsavedChanges: boolean,
    ) => {
      if (installation.source_id === null) return;
      marketUpdateRef.current?.abort();
      const controller = new AbortController();
      marketUpdateRef.current = controller;
      setMarketUpdatePending(true);
      try {
        const detail = await API.getMarketEntry(installation.source_id, installation.slug, {
          signal: controller.signal,
        });
        if (!controller.signal.aborted) {
          setMarketUpdateTarget({ entry: detail.entry, currentDefinition, hasUnsavedChanges });
        }
      } catch (e) {
        if (!controller.signal.aborted) pushToast(errMsg(e), "error");
      } finally {
        if (marketUpdateRef.current === controller) {
          marketUpdateRef.current = null;
          setMarketUpdatePending(false);
        }
      }
    },
    [pushToast],
  );

  const openMarket = useCallback(() => {
    const params = new URLSearchParams(search);
    params.set("section", "market");
    navigate(`${location}?${params.toString()}`, { replace: true });
  }, [location, navigate, search]);

  // --- 接线到供应商 ---

  const handleCreateProvider = useCallback(
    (definition: EndpointDefinition, endpointKey: string) => {
      const params = new URLSearchParams();
      params.set("section", "providers");
      params.set("custom", "new");
      params.set("endpoint", endpointKey);
      const baseUrl = definition.meta.hints?.base_url;
      if (baseUrl) params.set("base_url", baseUrl);
      navigate(`${location}?${params.toString()}`);
    },
    [location, navigate],
  );

  const handleNavigateToModel = useCallback(
    (reference: EndpointReference) => {
      const params = new URLSearchParams();
      params.set("section", "providers");
      params.set("custom", String(reference.provider_id));
      params.set("model", reference.model_id);
      navigate(`${location}?${params.toString()}`);
    },
    [location, navigate],
  );

  if (loadError) {
    return (
      <div role="alert" className="flex flex-col items-start gap-2.5 px-6 py-8">
        <span className="font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-warm">
          {t("common:load_failed")}
        </span>
        <p className="text-[12.5px] text-text-2">{loadError}</p>
        <button type="button" onClick={() => setReloadKey((k) => k + 1)} className={GHOST_BTN_CLS}>
          {t("common:retry")}
        </button>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 px-6 py-8 text-text-3">
        <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin text-accent-2" aria-hidden />
        <span className="font-mono text-[11px] uppercase tracking-[0.14em]">
          {t("common:loading")}
        </span>
      </div>
    );
  }

  return (
    <div className="flex">
      <nav
        aria-label={t("ce_section_title")}
        className="sticky top-0 max-h-screen w-60 shrink-0 self-start overflow-y-auto border-r border-hairline-soft px-3 py-5"
        style={{ background: "oklch(0.16 0.010 265 / 0.45)" }}
      >
        <div className="mb-3 flex items-center gap-1.5 px-1">
          <button
            type="button"
            onClick={() => select("new")}
            className={`${GHOST_BTN_CLS} flex-1 justify-center`}
          >
            <Plus className="h-3.5 w-3.5" aria-hidden />
            {t("ce_new")}
          </button>
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            title={t("ce_import_hint")}
            className={`${GHOST_BTN_CLS} flex-1 justify-center`}
          >
            <Upload className="h-3.5 w-3.5" aria-hidden />
            {t("ce_import")}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) void handleFilePicked(file);
            }}
          />
        </div>
        <button
          type="button"
          onClick={openMarket}
          title={t("ce_get_from_market_hint")}
          className={`${GHOST_BTN_CLS} mx-1 mb-3 w-[calc(100%-0.5rem)] justify-center border-dashed`}
        >
          <Store className="h-3.5 w-3.5" aria-hidden />
          {t("ce_get_from_market")}
        </button>

        {selectedKey === "new" && (
          <div className="mb-4">
            <div className={`${KICKER_CLS} mb-1.5 px-3`}>{t("ce_group_draft")}</div>
            <span className="mb-0.5 flex w-full items-center gap-2 rounded-[8px] border border-accent/35 bg-accent-dim px-3 py-2 text-[12.5px] text-text">
              {t("ce_new_endpoint")}
            </span>
          </div>
        )}

        {groups.map((group) =>
          group.entries.length === 0 ? null : (
            <div key={group.labelKey} className="mb-4">
              <div className={`${KICKER_CLS} mb-1.5 px-3`}>{t(group.labelKey)}</div>
              {group.entries.map((entry) => {
                const isActive = entry.key === selectedKey;
                return (
                  <button
                    key={entry.key}
                    type="button"
                    onClick={() => select(entry.key)}
                    aria-current={isActive ? "page" : undefined}
                    aria-pressed={isActive}
                    className={
                      "group relative mb-0.5 flex w-full items-center gap-2 rounded-[8px] border px-3 py-2 text-left text-[12.5px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent " +
                      (isActive
                        ? "border-accent/35 bg-accent-dim text-text shadow-[inset_0_1px_0_oklch(1_0_0_/_0.04),0_0_22px_-10px_var(--color-accent-glow)]"
                        : "border-transparent text-text-3 hover:border-hairline-soft hover:bg-bg-grad-a/55 hover:text-text")
                    }
                  >
                    <span
                      aria-hidden
                      className="absolute bottom-1.5 left-0 top-1.5 w-[2px] rounded-r-[2px]"
                      style={{
                        background:
                          "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))",
                        opacity: isActive ? 1 : 0,
                      }}
                    />
                    {entry.python ? (
                      <Lock className="h-3 w-3 shrink-0 text-text-3" aria-hidden />
                    ) : (
                      <FileJson2 className="h-3 w-3 shrink-0 text-text-3" aria-hidden />
                    )}
                    <span className="min-w-0 flex-1 truncate">{entry.label}</span>
                    {entry.referenceCount > 0 && (
                      <span className="shrink-0 text-[10px] text-text-3">
                        {entry.referenceCount}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          ),
        )}
      </nav>

      <div className="min-w-0 flex-1">
        {selection ? (
          <EndpointDetail
            // 市场更新会替换定义但保留端点键，安装时间随之变化，借此按新定义重建草稿。
            key={`${selectedKey ?? ""}:${selection.mode === "custom" ? (selection.record.installation?.installed_at ?? "") : ""}`}
            selection={selection}
            providers={providers}
            referenceCount={selectedKey ? (referenceCounts[selectedKey] ?? 0) : 0}
            onSaved={(record) => {
              voidCall(reload().then(() => select(record.key)));
            }}
            onDeleted={() => {
              voidCall(reload().then(() => select(null)));
            }}
            onCopied={(record) => {
              voidCall(reload().then(() => select(record.key)));
            }}
            onCreateProvider={handleCreateProvider}
            onNavigateToModel={handleNavigateToModel}
            onUpdateFromMarket={(installation, currentDefinition, hasUnsavedChanges) =>
              void handleUpdateFromMarket(installation, currentDefinition, hasUnsavedChanges)
            }
            marketUpdatePending={marketUpdatePending}
          />
        ) : (
          <p className="p-6 text-[12.5px] text-text-3">{t("ce_select_endpoint")}</p>
        )}
      </div>

      <EndpointImportDialog
        open={importOpen}
        fileName={importFileName}
        definition={importDefinition}
        validation={importValidation}
        busy={importBusy}
        onCreateCopy={() => void handleImportCreate()}
        onOverwrite={(id) => void handleImportOverwrite(id)}
        onCancel={() => setImportOpen(false)}
      />

      {marketUpdateTarget && (
        <MarketInstallDialog
          key={`${marketUpdateTarget.entry.source_id}/${marketUpdateTarget.entry.slug}`}
          entry={marketUpdateTarget.entry}
          currentEndpointDefinition={marketUpdateTarget.currentDefinition}
          hasUnsavedEndpointChanges={marketUpdateTarget.hasUnsavedChanges}
          onClose={() => setMarketUpdateTarget(null)}
          onInstallationChange={(installation) => {
            voidCall(reload().then(() => (installation === null ? select(null) : undefined)));
          }}
        />
      )}
    </div>
  );
}
