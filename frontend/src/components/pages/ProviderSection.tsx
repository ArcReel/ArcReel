import { useEffect, useMemo, useCallback } from "react";
import { useLocation, useSearch } from "wouter";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useProviderCatalog } from "@/hooks/useProviderCatalog";
import type { CatalogRefreshResult } from "@/hooks/useProviderCatalog";
import { useAppStore } from "@/stores/app-store";
import { ProviderIcon } from "@/components/ui/ProviderIcon";
import { ProviderDetail } from "./ProviderDetail";
import { CustomProviderSection } from "./settings/CustomProviderSection";
import { CustomProviderDetail } from "./settings/CustomProviderDetail";
import { CustomProviderForm } from "./settings/CustomProviderForm";

// ---------------------------------------------------------------------------
// Status dot — Darkroom palette
// ---------------------------------------------------------------------------

const STATUS_MAP: Record<string, { color: string; label: string; glow?: string }> = {
  ready: {
    color: "var(--color-good)",
    label: "status_ready",
    glow: "0 0 6px oklch(0.78 0.10 155 / 0.55)",
  },
  error: {
    color: "var(--color-warm)",
    label: "status_error",
    glow: "0 0 6px var(--color-warm-glow)",
  },
  unconfigured: {
    color: "var(--color-text-4)",
    label: "status_unconfigured",
  },
};

function StatusDot({ status }: { status: string }) {
  const { t } = useTranslation("dashboard");
  const { color, label, glow } = STATUS_MAP[status] ?? {
    color: "var(--color-text-4)",
    label: status,
  };
  return (
    <span
      className="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
      role="img"
      aria-label={t(label)}
      style={{ background: color, boxShadow: glow }}
    />
  );
}

// ---------------------------------------------------------------------------
// Provider Section
// ---------------------------------------------------------------------------

type Selection =
  | { kind: "preset"; id: string }
  | { kind: "custom"; id: number }
  | { kind: "new-custom" }
  | null;

export function ProviderSection() {
  const { t, i18n } = useTranslation(["dashboard", "common"]);
  const { providers, customProviders, loading, error: loadError, reload, refresh } = useProviderCatalog(i18n.language);
  const [location, navigate] = useLocation();
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

  const setSelection = useCallback(
    (sel: Selection) => {
      const p = new URLSearchParams(search);
      p.delete("provider");
      p.delete("custom");
      p.delete("model");
      if (sel?.kind === "preset") p.set("provider", sel.id);
      else if (sel?.kind === "custom") p.set("custom", String(sel.id));
      else if (sel?.kind === "new-custom") p.set("custom", "new");
      navigate(`${location}?${p.toString()}`, { replace: true });
    },
    [search, location, navigate],
  );

  // 从「调用端点」小节的「新建供应商并使用此端点」接线过来的预填。
  const prefill = useMemo(() => {
    const params = new URLSearchParams(search);
    return {
      baseUrl: params.get("base_url") ?? undefined,
      endpoint: params.get("endpoint") ?? undefined,
    };
  }, [search]);

  // 首个 preset 兜底选中：拉取完成后 URL 仍未指定选中项时补一次。
  useEffect(() => {
    if (loading || selection || providers.length === 0) return;
    setSelection({ kind: "preset", id: providers[0].id });
  }, [loading, selection, providers, setSelection]);

  if (loadError) {
    return (
      <div role="alert" className="flex flex-col items-start gap-2.5 px-6 py-8">
        <span className="inline-flex items-center gap-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-warm">
          {t("common:load_failed")}
        </span>
        <p className="text-[12.5px] text-text-2">{loadError}</p>
        <button
          type="button"
          onClick={reload}
          className="rounded-[7px] border border-hairline-soft bg-bg-grad-a/55 px-3 py-1.5 text-[12px] text-text-2 transition-colors hover:border-hairline hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
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
          {t("loading_providers")}
        </span>
      </div>
    );
  }

  return (
    <div className="flex">
      {/* Provider list sidebar */}
      <nav
        aria-label={t("provider_list")}
        className="sticky top-0 max-h-screen w-56 shrink-0 self-start overflow-y-auto border-r border-hairline-soft px-3 py-5"
        style={{ background: "oklch(0.16 0.010 265 / 0.45)" }}
      >
        <div className="mb-2 px-3 font-mono text-[9.5px] font-bold uppercase tracking-[0.16em] text-text-4">
          {t("preset_providers")}
        </div>
        {providers.map((p) => {
          const isActive =
            selection?.kind === "preset" && selection.id === p.id;
          return (
            <button
              key={p.id}
              type="button"
              onClick={() => setSelection({ kind: "preset", id: p.id })}
              aria-current={isActive ? "page" : undefined}
              aria-pressed={isActive}
              className={
                "group relative mb-0.5 flex w-full items-center gap-2.5 rounded-[8px] border px-3 py-2 text-left text-[12.5px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent " +
                (isActive
                  ? "border-accent/35 bg-accent-dim text-text shadow-[inset_0_1px_0_oklch(1_0_0_/_0.04),0_0_22px_-10px_var(--color-accent-glow)]"
                  : "border-transparent text-text-3 hover:border-hairline-soft hover:bg-bg-grad-a/55 hover:text-text")
              }
            >
              {/* Active rail */}
              <span
                aria-hidden
                className="absolute left-0 top-1.5 bottom-1.5 w-[2px] rounded-r-[2px] transition-opacity"
                style={{
                  background:
                    "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))",
                  opacity: isActive ? 1 : 0,
                }}
              />
              <ProviderIcon providerId={p.id} className="h-3.5 w-3.5 shrink-0" />
              <span className="min-w-0 flex-1 truncate">{p.display_name}</span>
              <StatusDot status={p.status} />
            </button>
          );
        })}

        {/* Custom providers */}
        <CustomProviderSection
          providers={customProviders}
          selectedId={selection?.kind === "custom" ? selection.id : null}
          onSelect={(id) => setSelection({ kind: "custom", id })}
          onAdd={() => setSelection({ kind: "new-custom" })}
        />
      </nav>

      {/* Detail panel */}
      <div className="min-w-0 flex-1">
        {selection?.kind === "preset" && (
          <div className="p-6">
            <ProviderDetail providerId={selection.id} onSaved={refreshAfterSave} />
          </div>
        )}
        {selection?.kind === "custom" && (
          <CustomProviderDetail
            providerId={selection.id}
            initialModelId={modelId}
            onDeleted={() => {
              void refresh();
              if (providers.length > 0) {
                setSelection({ kind: "preset", id: providers[0].id });
              } else {
                setSelection(null);
              }
            }}
            onSaved={refreshAfterSave}
          />
        )}
        {selection?.kind === "new-custom" && (
          <CustomProviderForm
            initialBaseUrl={prefill.baseUrl}
            initialEndpoint={prefill.endpoint}
            onSaved={() => {
              void refresh().then((result) => {
                if (result.status !== "ok") {
                  notifyRefreshFailure(result);
                  return;
                }
                const list = result.customProviders;
                if (list.length > 0) {
                  setSelection({ kind: "custom", id: list[list.length - 1].id });
                }
              });
            }}
            onCancel={() => {
              if (providers.length > 0) {
                setSelection({ kind: "preset", id: providers[0].id });
              } else {
                setSelection(null);
              }
            }}
          />
        )}
        {!selection && (
          <div className="p-6 text-[12.5px] text-text-3">{t("select_provider")}</div>
        )}
      </div>
    </div>
  );
}
