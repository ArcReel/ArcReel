import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useSearch } from "wouter";
import { FileJson2, Loader2, Lock, Plus, Upload } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { errMsg, voidCall } from "@/utils/async";
import { useAppStore } from "@/stores/app-store";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import { GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";
import type {
  CustomEndpointInfo,
  CustomProviderInfo,
  EndpointDefinition,
  EndpointDescriptor,
  EndpointValidateResponse,
} from "@/types";
import { newEndpointDefinition } from "./endpoint-definition-draft";
import { EndpointDetail, type EndpointSelection } from "./EndpointDetail";
import { EndpointImportDialog } from "./EndpointImportDialog";

const KICKER_CLS = "font-mono text-[9.5px] font-bold uppercase tracking-[0.16em] text-text-4";

interface ListEntry {
  key: string;
  label: string;
  python: boolean;
  referenceCount: number;
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
  const [importDefinition, setImportDefinition] = useState<EndpointDefinition | null>(null);
  const [importValidation, setImportValidation] = useState<EndpointValidateResponse | null>(null);
  const [importBusy, setImportBusy] = useState(false);
  const [importOpen, setImportOpen] = useState(false);

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
        entries: catalog.filter((e) => e.source === "custom").map(toEntry),
      },
      {
        labelKey: "ce_group_builtin",
        entries: catalog
          .filter((e) => e.source === "builtin" && e.kind === "declarative")
          .map(toEntry),
      },
      {
        labelKey: "ce_group_builtin_python",
        entries: catalog.filter((e) => e.kind === "python").map(toEntry),
      },
    ];
  }, [catalog, referenceCounts, t]);

  const selection = useMemo((): EndpointSelection | null => {
    if (selectedKey === "new") {
      return { mode: "new", definition: newEndpointDefinition("") };
    }
    const record = customEndpoints.find((e) => e.key === selectedKey);
    if (record) return { mode: "custom", record };
    const descriptor = catalog.find((e) => e.key === selectedKey);
    if (!descriptor) return null;
    return descriptor.kind === "python"
      ? { mode: "python", descriptor }
      : { mode: "builtin", descriptor };
  }, [selectedKey, customEndpoints, catalog]);

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
        const parsed = JSON.parse(await file.text()) as EndpointDefinition;
        if (importRunRef.current !== run) return;
        setImportDefinition(parsed);
        const result = await API.validateCustomEndpoint(parsed);
        if (importRunRef.current !== run) return;
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
            key={selectedKey ?? ""}
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
    </div>
  );
}
