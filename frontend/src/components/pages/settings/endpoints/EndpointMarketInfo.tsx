import { Loader2, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import type { EndpointInstallation } from "@/types";
import { MarketInstallBadges } from "../market/MarketInstallBadges";

/** Two-axis status badge; hide for endpoints not installed from the market. Shared by both kind detail headers. */
export function EndpointInstallBadges({ installation }: { installation: EndpointInstallation | null }) {
  if (installation === null) return null;
  return <MarketInstallBadges state={installation.state} modified={installation.modified} />;
}

/**
 * Installation-source description: deleted sources retain only the raw canonical key; annotate both disabled and deleted sources.
 * Hide for non-market installations. Shared by both kind detail headers.
 */
export function MarketOrigin({ installation }: { installation: EndpointInstallation | null }) {
  const { t } = useTranslation("dashboard");
  if (installation === null) return null;
  const source = installation.source_display_name ?? installation.source_key;
  const key =
    installation.source_enabled === null
      ? "ce_from_market_source_deleted"
      : installation.source_enabled
        ? "ce_from_market"
        : "ce_from_market_source_disabled";
  return <span className="min-w-0 break-all">{t(key, { source })}</span>;
}

/**
 * Update action in the detail row: show only when the market axis is updateable; the parent fetches entry details and opens installation in update mode.
 * Show a spinner and disable while loading entry details.
 */
export function MarketUpdateButton({
  installation,
  pending,
  disabled = false,
  onUpdate,
}: {
  installation: EndpointInstallation | null;
  pending: boolean;
  disabled?: boolean;
  onUpdate: (installation: EndpointInstallation) => void;
}) {
  const { t } = useTranslation("dashboard");
  if (installation?.state !== "update_available") return null;
  return (
    <Button variant="outline" onClick={() => onUpdate(installation)} disabled={pending || disabled}>
      {pending ? (
        <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
      ) : (
        <RefreshCw aria-hidden data-icon="inline-start" />
      )}
      {t("market_update")}
    </Button>
  );
}
