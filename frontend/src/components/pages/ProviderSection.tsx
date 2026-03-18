import { useState, useEffect } from "react";
import { Loader2 } from "lucide-react";
import { API } from "@/api";
import type { ProviderInfo } from "@/types";
import { ProviderDetail } from "./ProviderDetail";

// ---------------------------------------------------------------------------
// Status dot
// ---------------------------------------------------------------------------

function StatusDot({ status }: { status: string }) {
  const color =
    status === "ready"
      ? "bg-green-400"
      : status === "error"
        ? "bg-yellow-400"
        : "bg-gray-500";
  return <span className={`h-2 w-2 shrink-0 rounded-full ${color}`} />;
}

// ---------------------------------------------------------------------------
// Provider Section
// ---------------------------------------------------------------------------

export function ProviderSection() {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    API.getProviders().then((res) => {
      setProviders(res.providers);
      if (res.providers.length > 0) {
        setSelectedId((prev) => prev ?? res.providers[0].id);
      }
      setLoading(false);
    });
  }, []);

  const refresh = async () => {
    const res = await API.getProviders();
    setProviders(res.providers);
  };

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
      {/* Provider list */}
      <div className="w-52 shrink-0 border-r border-gray-800 py-3">
        <div className="px-4 pb-2 text-xs uppercase tracking-wide text-gray-500">
          供应商列表
        </div>
        {providers.map((p) => (
          <button
            key={p.id}
            type="button"
            onClick={() => setSelectedId(p.id)}
            className={`flex w-full items-center justify-between px-4 py-2.5 text-left text-sm transition-colors ${
              selectedId === p.id
                ? "border-l-2 border-indigo-500 bg-gray-800/50 text-white"
                : "border-l-2 border-transparent text-gray-400 hover:bg-gray-800/30 hover:text-gray-200"
            }`}
          >
            <span className="truncate">{p.display_name}</span>
            <StatusDot status={p.status} />
          </button>
        ))}
      </div>

      {/* Provider detail */}
      <div className="flex-1 overflow-y-auto p-6">
        {selectedId ? (
          <ProviderDetail providerId={selectedId} onSaved={() => void refresh()} />
        ) : (
          <div className="text-sm text-gray-500">请选择供应商</div>
        )}
      </div>
    </div>
  );
}
