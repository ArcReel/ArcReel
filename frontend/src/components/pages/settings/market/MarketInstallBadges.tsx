import { AlertTriangle, Check } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import type { MarketInstallationState } from "@/types";

/** 已安装端点的两轴徽标：市场轴（已安装 / 可更新 / 市场中不可用）× 本地修改轴（已修改）。 */
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
