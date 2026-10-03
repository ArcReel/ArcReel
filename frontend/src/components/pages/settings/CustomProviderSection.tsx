import { Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { CustomProviderInfo } from "@/types";

// ---------------------------------------------------------------------------
// Status dot (replicates preset provider pattern)
// ---------------------------------------------------------------------------

function CustomStatusDot({ provider }: { provider: CustomProviderInfo }) {
  const { t } = useTranslation("dashboard");
  const ready = provider.base_url && provider.api_key_masked;
  const color = ready ? "bg-good" : "bg-muted-foreground";
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
    <div className="mt-3 border-t border-border pt-3">
      <div className="px-4 pb-2 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-muted-foreground">
        {t("custom_providers")}
      </div>
      {providers.map((p) => (
        <button
          key={p.id}
          type="button"
          onClick={() => onSelect(p.id)}
          className={`flex w-full items-center gap-2.5 px-4 py-2.5 text-left text-sm transition-colors ${
            selectedId === p.id
              ? "border-l-2 border-primary bg-primary/12 text-foreground shadow-[inset_0_1px_0_oklch(1_0_0_/_0.05)]"
              : "border-l-2 border-transparent text-muted-foreground hover:bg-card/40 hover:text-foreground"
          }`}
        >
          {/* 自定义 provider 恒用字母徽章，不按 display_name 猜品牌：中转站协议无关，
              打某品牌图标会名不副实，且自由文本名匹配对中文名割裂。将来若要品牌化，
              走用户显式选图标，而非名字猜测。 */}
          <span className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-sm border border-border/50 bg-sidebar/70 font-mono text-[10px] font-bold uppercase text-subtle-foreground">
            {Array.from(p.display_name)[0] ?? "?"}
          </span>
          <span className="min-w-0 flex-1 truncate">{p.display_name}</span>
          <CustomStatusDot provider={p} />
        </button>
      ))}
      <button
        type="button"
        onClick={onAdd}
        className="flex w-full items-center gap-2.5 px-4 py-2.5 text-left text-sm text-muted-foreground transition-colors hover:bg-card/40 hover:text-subtle-foreground"
      >
        <Plus className="h-4 w-4 shrink-0" aria-hidden="true" />
        <span>{t("add_custom_provider")}</span>
      </button>
    </div>
  );
}
