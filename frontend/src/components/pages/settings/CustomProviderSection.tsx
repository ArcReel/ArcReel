import { Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { CustomProviderInfo } from "@/types";

// ---------------------------------------------------------------------------
// Status dot (replicates preset provider pattern)
// ---------------------------------------------------------------------------

function CustomStatusDot({ provider }: { provider: CustomProviderInfo }) {
  const { t } = useTranslation("dashboard");
  const ready = provider.base_url && provider.api_key_masked;
  const color = ready ? "bg-green-400" : "bg-gray-500";
  const label = ready ? t("status_connected") : t("status_unconfigured");
  return <span className={`h-2 w-2 shrink-0 rounded-full ${color}`} role="img" aria-label={label} />;
}

// ---------------------------------------------------------------------------
// Sidebar section for custom providers
// ---------------------------------------------------------------------------

interface CustomProviderSectionProps {
  providers: CustomProviderInfo[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  onAdd: () => void;
}

export function CustomProviderSection({ providers, selectedId, onSelect, onAdd }: CustomProviderSectionProps) {
  const { t } = useTranslation("dashboard");
  return (
    <div className="mt-3 border-t border-gray-800 pt-3">
      <div className="px-4 pb-2 text-xs uppercase tracking-wide text-gray-500">
        {t("custom_providers")}
      </div>
      {providers.map((p) => (
        <button
          key={p.id}
          type="button"
          onClick={() => onSelect(p.id)}
          className={`flex w-full items-center gap-2.5 px-4 py-2.5 text-left text-sm transition-colors ${
            selectedId === p.id
              ? "border-l-2 border-indigo-500 bg-gray-800/50 text-white"
              : "border-l-2 border-transparent text-gray-400 hover:bg-gray-800/30 hover:text-gray-200"
          }`}
        >
          <span className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded bg-gray-700 text-[10px] font-bold uppercase text-gray-300">
            {p.display_name?.[0] ?? "?"}
          </span>
          <span className="min-w-0 flex-1 truncate">{p.display_name}</span>
          <CustomStatusDot provider={p} />
        </button>
      ))}
      <button
        type="button"
        onClick={onAdd}
        className="flex w-full items-center gap-2.5 px-4 py-2.5 text-left text-sm text-gray-500 transition-colors hover:bg-gray-800/30 hover:text-gray-300"
      >
        <Plus className="h-4 w-4 shrink-0" aria-hidden="true" />
        <span>{t("add_custom_provider")}</span>
      </button>
    </div>
  );
}
