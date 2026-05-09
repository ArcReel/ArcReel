import { Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { CustomProviderInfo } from "@/types";

// ---------------------------------------------------------------------------
// Status dot (replicates preset provider pattern)
// ---------------------------------------------------------------------------

function CustomStatusDot({ provider }: { provider: CustomProviderInfo }) {
  const { t } = useTranslation("dashboard");
  const ready = provider.base_url && provider.api_key_masked;
  const color = ready ? "bg-good" : "bg-text-4";
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
    <div className="mt-3 border-t border-hairline pt-3">
      <div className="px-4 pb-2 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4">
        {t("custom_providers")}
      </div>
      {providers.map((p) => (
        <button
          key={p.id}
          type="button"
          onClick={() => onSelect(p.id)}
          className={`flex w-full items-center gap-2.5 px-4 py-2.5 text-left text-sm transition-colors ${
            selectedId === p.id
              ? "border-l-2 border-accent bg-accent-dim text-text shadow-[inset_0_1px_0_oklch(1_0_0_/_0.05)]"
              : "border-l-2 border-transparent text-text-3 hover:bg-bg-grad-a/40 hover:text-text"
          }`}
        >
          <span className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded border border-hairline-soft bg-bg-grad-b/70 font-mono text-[10px] font-bold uppercase text-text-2">
            {p.display_name?.[0] ?? "?"}
          </span>
          <span className="min-w-0 flex-1 truncate">{p.display_name}</span>
          <CustomStatusDot provider={p} />
        </button>
      ))}
      <button
        type="button"
        onClick={onAdd}
        className="flex w-full items-center gap-2.5 px-4 py-2.5 text-left text-sm text-text-4 transition-colors hover:bg-bg-grad-a/40 hover:text-text-2"
      >
        <Plus className="h-4 w-4 shrink-0" aria-hidden="true" />
        <span>{t("add_custom_provider")}</span>
      </button>
    </div>
  );
}
