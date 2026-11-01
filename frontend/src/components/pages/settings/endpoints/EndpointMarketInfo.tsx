import { Loader2, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import type { EndpointInstallation } from "@/types";
import { MarketInstallBadges } from "../market/MarketInstallBadges";

/** 两轴状态徽标；不是从市场安装的端点不显示。两种 kind 的详情页头共用。 */
export function EndpointInstallBadges({ installation }: { installation: EndpointInstallation | null }) {
  if (installation === null) return null;
  return <MarketInstallBadges state={installation.state} modified={installation.modified} />;
}

/**
 * 安装记录的来源描述：来源被删除时只剩规范键原文，禁用或删除都注明。
 * 不是从市场安装的端点不显示。两种 kind 的详情页头共用。
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
 * 详情动作行的「更新」：只在市场轴可更新时出现，点击后由上层拉取条目详情并打开安装弹窗的更新态。
 * 条目详情加载期间转圈并禁用。
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
