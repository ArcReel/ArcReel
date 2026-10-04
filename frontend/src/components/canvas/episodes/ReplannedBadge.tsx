import { useTranslation } from "react-i18next";

/**
 * 集规划状态为 stale 的标记「原文已重新规划」。
 *
 * 与产物过期（琥珀色实底）区分：品牌色虚线框，只说这一集的原文动过，不说产物旧了。
 */
export function ReplannedBadge() {
  const { t } = useTranslation("dashboard");
  return (
    <span className="inline-flex shrink-0 items-center rounded-sm border border-dashed border-primary/60 px-1.5 text-xs text-primary">
      {t("episodes_view_replanned")}
      <span className="sr-only">{t("episodes_view_replanned_hint")}</span>
    </span>
  );
}
