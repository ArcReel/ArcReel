import { useTranslation } from "react-i18next";

export function ReplannedBadge() {
  const { t } = useTranslation("dashboard");
  return (
    <span className="inline-flex shrink-0 items-center rounded-sm border border-dashed border-primary/60 px-1.5 text-xs text-primary">
      {t("episodes_view_replanned")}
      <span className="sr-only">{t("episodes_view_replanned_hint")}</span>
    </span>
  );
}
