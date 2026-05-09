import { useState, useEffect, useMemo, useCallback } from "react";
import { voidCall } from "@/utils/async";
import { useLocation, useSearch } from "wouter";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { ProviderIcon } from "@/components/ui/ProviderIcon";
import type { ProviderInfo, CustomProviderInfo } from "@/types";
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
  const { t } = useTranslation("dashboard");
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [customProviders, setCustomProviders] = useState<CustomProviderInfo[]>([]);
  const [loading, setLoading] = useState(true);
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

  const setSelection = useCallback(
    (sel: Selection) => {
      const p = new URLSearchParams(search);
      p.delete("provider");
      p.delete("custom");
      if (sel?.kind === "preset") p.set("provider", sel.id);
      else if (sel?.kind === "custom") p.set("custom", String(sel.id));
      else if (sel?.kind === "new-custom") p.set("custom", "new");
      navigate(`${location}?${p.toString()}`, { replace: true });
    },
    [search, location, navigate],
  );

  const refreshPreset = useCallback(async () => {
    const res = await API.getProviders();
    setProviders(res.providers);
  }, []);

  const refreshCustom = useCallback(async () => {
    const res = await API.listCustomProviders();
    setCustomProviders(res.providers);
  }, []);

  useEffect(() => {
    let disposed = false;
    voidCall(
      Promise.all([API.getProviders(), API.listCustomProviders()]).then(
        ([presetRes, customRes]) => {
          if (disposed) return;
          setProviders(presetRes.providers);
          setCustomProviders(customRes.providers);
          const params = new URLSearchParams(search);
          if (
            !params.get("provider") &&
            !params.get("custom") &&
            presetRes.providers.length > 0
          ) {
            setSelection({ kind: "preset", id: presetRes.providers[0].id });
          }
          setLoading(false);
        },
      ),
    );
    return () => {
      disposed = true;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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
            <ProviderDetail providerId={selection.id} onSaved={() => void refreshPreset()} />
          </div>
        )}
        {selection?.kind === "custom" && (
          <CustomProviderDetail
            providerId={selection.id}
            onDeleted={() => {
              void refreshCustom();
              if (providers.length > 0) {
                setSelection({ kind: "preset", id: providers[0].id });
              } else {
                setSelection(null);
              }
            }}
            onSaved={() => void refreshCustom()}
          />
        )}
        {selection?.kind === "new-custom" && (
          <CustomProviderForm
            onSaved={() => {
              void API.listCustomProviders()
                .then((res) => {
                  setCustomProviders(res.providers);
                  if (res.providers.length > 0) {
                    const newest = res.providers[res.providers.length - 1];
                    setSelection({ kind: "custom", id: newest.id });
                  }
                })
                .catch(() => void refreshCustom());
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
