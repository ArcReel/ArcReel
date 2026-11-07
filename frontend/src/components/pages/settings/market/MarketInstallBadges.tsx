import { AlertTriangle, Check } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import type { MarketInstallationState } from "@/types";

/** Two-axis installed-endpoint badges: market state (installed / update available / unavailable in market) × local modifications (modified). */
export function MarketInstallBadges({ state, modified }: { state: MarketInstallationState; modified: boolean }) {
  const { t } = useTranslation("dashboard");
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {state === "current" && (
        <Badge variant="outline">
          <Check data-icon="inline-start" className="text-good" aria-hidden />
          {t("market_installed")}
        </Badge>
      )}
      {state === "update_available" && <Badge>{t("market_update_available")}</Badge>}
      {state === "unavailable" && (
        <Badge variant="outline">
          <AlertTriangle data-icon="inline-start" className="text-warn" aria-hidden />
          {t("market_unavailable")}
        </Badge>
      )}
      {modified && <Badge variant="secondary">{t("market_modified")}</Badge>}
    </span>
  );
}
