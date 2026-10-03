import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { MarketSourceInfo, MarketSourceStatus } from "@/types";

export const KICKER_ACCENT_CLS =
  "font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-primary";

export const KICKER_CLS = "font-mono text-[9.5px] font-bold uppercase tracking-[0.16em] text-muted-foreground";

const STATUS_DOT_CLS: Record<MarketSourceStatus, string> = {
  ok: "bg-good",
  never_fetched: "bg-muted-foreground",
  unreachable: "bg-warn",
  invalid_index: "bg-destructive",
  unsupported_schema: "bg-destructive",
};

/** 市场源状态点：刷新中显示转圈，禁用的源淡化。 */
export function SourceStatusDot({
  source,
  refreshing,
}: {
  source: MarketSourceInfo;
  refreshing: boolean;
}) {
  const { t } = useTranslation("dashboard");
  if (refreshing) {
    return (
      <Loader2
        className="h-3 w-3 shrink-0 animate-spin text-muted-foreground"
        role="img"
        aria-label={t("market_refreshing")}
      />
    );
  }
  return (
    <span
      role="img"
      aria-label={t(`market_status_${source.status}`)}
      className={`inline-block h-2 w-2 shrink-0 rounded-full ${STATUS_DOT_CLS[source.status]} ${
        source.is_enabled ? "" : "opacity-40"
      }`}
    />
  );
}
