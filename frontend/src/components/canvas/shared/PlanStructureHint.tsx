import { useTranslation } from "react-i18next";

export function PlanStructureHint() {
  const { t } = useTranslation("dashboard");
  return <p className="px-1 text-xs text-muted-foreground">{t("review_structure_hint")}</p>;
}
