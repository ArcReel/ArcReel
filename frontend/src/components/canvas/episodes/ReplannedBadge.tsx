import { useTranslation } from "react-i18next";

/**
 * Source-replanned marker for stale episode planning.
 *
 * Distinct from stale artifacts (solid amber): a brand-colored dashed border indicates changed episode source, without claiming artifacts are stale.
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
