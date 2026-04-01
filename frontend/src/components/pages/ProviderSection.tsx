import { useState, useEffect, useMemo, useCallback } from "react";
import { useLocation, useSearch } from "wouter";
import { Loader2 } from "lucide-react";
import { API } from "@/api";
import { ProviderIcon } from "@/components/ui/ProviderIcon";
import type { ProviderInfo, CustomProviderInfo } from "@/types";
import { ProviderDetail } from "./ProviderDetail";
import { CustomProviderSection } from "./settings/CustomProviderSection";
import { CustomProviderDetail } from "./settings/CustomProviderDetail";
import { CustomProviderForm } from "./settings/CustomProviderForm";

// ---------------------------------------------------------------------------
// Status dot
// ---------------------------------------------------------------------------

const STATUS_MAP: Record<string, { color: string; label: string }> = {
  ready: { color: "bg-green-400", label: "已就绪" },
  error: { color: "bg-yellow-400", label: "异常" },
  unconfigured: { color: "bg-gray-500", label: "未配置" },
};

function StatusDot({ status }: { status: string }) {
  const { color, label } = STATUS_MAP[status] ?? { color: "bg-gray-500", label: status };
  return <span className={`h-2 w-2 shrink-0 rounded-full ${color}`} role="img" aria-label={label} />;
}

// ---------------------------------------------------------------------------
// Provider Section
// ---------------------------------------------------------------------------

// Selection can be a preset provider (string id) or custom provider (numeric id) or "new" form
type Selection =
  | { kind: "preset"; id: string }
  | { kind: "custom"; id: number }
  | { kind: "new-custom" }
  | null;

export function ProviderSection() {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [customProviders, setCustomProviders] = useState<CustomProviderInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [location, navigate] = useLocation();
  const search = useSearch();

  // Parse URL-driven selection into typed Selection
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
      // Clear both params, then set the relevant one
      p.delete("provider");
      p.delete("custom");
      if (sel?.kind === "preset") p.set("provider", sel.id);
      else if (sel?.kind === "custom") p.set("custom", String(sel.id));
      else if (sel?.kind === "new-custom") p.set("custom", "new");
      navigate(`${location}?${p.toString()}`, { replace: true });
    },
    [search, location, navigate],
  );

  // Fetch preset providers
  const refreshPreset = useCallback(async () => {
    const res = await API.getProviders();
    setProviders(res.providers);
  }, []);

  // Fetch custom providers
  const refreshCustom = useCallback(async () => {
    const res = await API.listCustomProviders();
    setCustomProviders(res.providers);
  }, []);

  useEffect(() => {
    let disposed = false;
    Promise.all([API.getProviders(), API.listCustomProviders()]).then(([presetRes, customRes]) => {
      if (disposed) return;
      setProviders(presetRes.providers);
      setCustomProviders(customRes.providers);
      // Auto-select first preset if nothing is selected
      const params = new URLSearchParams(search);
      if (!params.get("provider") && !params.get("custom") && presetRes.providers.length > 0) {
        setSelection({ kind: "preset", id: presetRes.providers[0].id });
      }
      setLoading(false);
    });
    return () => {
      disposed = true;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (loading) {
    return (
      <div className="flex items-center gap-2 px-6 py-8 text-sm text-gray-500">
        <Loader2 className="h-4 w-4 animate-spin" />
        加载供应商列表…
      </div>
    );
  }

  return (
    <div className="flex h-full">
      {/* Provider list sidebar */}
      <nav aria-label="供应商列表" className="w-52 shrink-0 overflow-y-auto border-r border-gray-800 py-3">
        {/* Preset providers */}
        <div className="px-4 pb-2 text-xs uppercase tracking-wide text-gray-500">
          预置供应商
        </div>
        {providers.map((p) => (
          <button
            key={p.id}
            type="button"
            onClick={() => setSelection({ kind: "preset", id: p.id })}
            className={`flex w-full items-center gap-2.5 px-4 py-2.5 text-left text-sm transition-colors ${
              selection?.kind === "preset" && selection.id === p.id
                ? "border-l-2 border-indigo-500 bg-gray-800/50 text-white"
                : "border-l-2 border-transparent text-gray-400 hover:bg-gray-800/30 hover:text-gray-200"
            }`}
          >
            <ProviderIcon providerId={p.id} className="h-4 w-4 shrink-0" />
            <span className="min-w-0 flex-1 truncate">{p.display_name}</span>
            <StatusDot status={p.status} />
          </button>
        ))}

        {/* Custom providers */}
        <CustomProviderSection
          providers={customProviders}
          selectedId={selection?.kind === "custom" ? selection.id : null}
          onSelect={(id) => setSelection({ kind: "custom", id })}
          onAdd={() => setSelection({ kind: "new-custom" })}
        />
      </nav>

      {/* Detail panel — custom provider views manage their own scroll + fixed bottom bar */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {selection?.kind === "preset" && (
          <div className="flex-1 overflow-y-auto p-6">
            <ProviderDetail providerId={selection.id} onSaved={() => void refreshPreset()} />
          </div>
        )}
        {selection?.kind === "custom" && (
          <CustomProviderDetail
            providerId={selection.id}
            onDeleted={() => {
              void refreshCustom();
              // Select first preset provider after delete
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
              // After save, re-fetch to get latest list and select the new one
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
          <div className="flex-1 overflow-y-auto p-6">
            <div className="text-sm text-gray-500">请选择供应商</div>
          </div>
        )}
      </div>
    </div>
  );
}
