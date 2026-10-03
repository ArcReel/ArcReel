import { useEffect, useMemo, useCallback } from "react";
import { useLocation, useSearch } from "wouter";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useProviderCatalog } from "@/hooks/useProviderCatalog";
import type { CatalogRefreshResult } from "@/hooks/useProviderCatalog";
import { useAppStore } from "@/stores/app-store";
import { ProviderIcon } from "@/components/shared/ProviderIcon";
import { ProviderDetail } from "./ProviderDetail";
import { CustomProviderSection } from "./settings/CustomProviderSection";
import { CustomProviderDetail } from "./settings/CustomProviderDetail";
import { CustomProviderForm } from "./settings/CustomProviderForm";

// ---------------------------------------------------------------------------
// Status dot — Darkroom palette
// ---------------------------------------------------------------------------

const STATUS_MAP: Record<string, { color: string; label: string; glow?: string }> = {
  ready: {
    color: "var(--good)",
    label: "status_ready",
    glow: "0 0 6px oklch(0.78 0.10 155 / 0.55)",
  },
  error: {
    color: "var(--warn)",
    label: "status_error",
    glow: "0 0 6px color-mix(in oklab, var(--warn) 35%, transparent)",
  },
  unconfigured: {
    color: "var(--muted-foreground)",
    label: "status_unconfigured",
  },
};

function StatusDot({ status }: { status: string }) {
  const { t } = useTranslation("dashboard");
  const { color, label, glow } = STATUS_MAP[status] ?? {
    color: "var(--muted-foreground)",
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
        <span className="inline-flex items-center gap-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-warn">
          {t("common:load_failed")}
        </span>
        <p className="text-[12.5px] text-subtle-foreground">{loadError}</p>
        <button
          type="button"
          onClick={reload}
          className="rounded-md border border-border/50 bg-card/55 px-3 py-1.5 text-[12px] text-subtle-foreground transition-colors hover:border-border hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {t("common:retry")}
        </button>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 px-6 py-8 text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin text-primary" aria-hidden />
        <span className="font-mono text-[11px] uppercase tracking-[0.14em]">
          {t("loading_providers")}
        </span>
      </div>
    );
  }

  return (
    // 全出血档：二级栏与详情栏各自滚动
    <div className="flex min-h-0 flex-1">
      {/* Provider list sidebar */}
      <nav
        aria-label={t("provider_list")}
        className="relative w-56 shrink-0 overflow-y-auto border-r border-border/50 px-3 py-5"
        style={{ background: "oklch(0.16 0.010 265 / 0.45)" }}
      >
        <div className="mb-2 px-3 font-mono text-[9.5px] font-bold uppercase tracking-[0.16em] text-muted-foreground">
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
                "group relative mb-0.5 flex w-full items-center gap-2.5 rounded-md border px-3 py-2 text-left text-[12.5px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
                (isActive
                  ? "border-primary/35 bg-primary/12 text-foreground shadow-[inset_0_1px_0_oklch(1_0_0_/_0.04),0_0_22px_-10px_color-mix(in_oklab,var(--primary)_35%,transparent)]"
                  : "border-transparent text-muted-foreground hover:border-border/50 hover:bg-card/55 hover:text-foreground")
              }
            >
              {/* Active rail */}
              <span
                aria-hidden
                className="absolute left-0 top-1.5 bottom-1.5 w-[2px] rounded-r-xs transition-opacity"
                style={{
                  background:
                    "var(--primary)",
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
      <div className="relative min-w-0 flex-1 overflow-y-auto [scrollbar-gutter:stable]">
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
            onSaved={(created) => {
              // 选中用新建响应带回的 id，不等目录重取的结局：重取被后续请求接管时，
              // 用户会留在填满的新建表单上，再保存一次就多出一个重复供应商。
              if (created) setSelection({ kind: "custom", id: created.id });
              refreshAfterSave();
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
          <div className="p-6 text-[12.5px] text-muted-foreground">{t("select_provider")}</div>
        )}
      </div>
    </div>
  );
}
