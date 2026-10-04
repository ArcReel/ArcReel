import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import type { MarketSourceInfo, MarketSourceStatus } from "@/types";

const STATUS_DOT_CLS: Record<MarketSourceStatus, string> = {
  ok: "bg-good",
  never_fetched: "bg-muted-foreground",
  unreachable: "bg-warn",
  invalid_index: "bg-destructive",
  unsupported_schema: "bg-destructive",
};

/** 市场源状态点：刷新中显示转圈；停用的源显示为空心点，读屏读「已停用」。 */
export function SourceStatusDot({ source, refreshing }: { source: MarketSourceInfo; refreshing: boolean }) {
  const { t } = useTranslation("dashboard");
  if (refreshing) {
    return (
      <Loader2 className="size-3 shrink-0 animate-spin text-muted-foreground" role="img" aria-label={t("market_refreshing")} />
    );
  }
  if (!source.is_enabled) {
    return (
      <span
        role="img"
        aria-label={t("market_source_status_disabled")}
        className="inline-block size-2 shrink-0 rounded-full border border-muted-foreground"
      />
    );
  }
  return (
    <span
      role="img"
      aria-label={t(`market_status_${source.status}`)}
      className={cn("inline-block size-2 shrink-0 rounded-full", STATUS_DOT_CLS[source.status])}
    />
  );
}
