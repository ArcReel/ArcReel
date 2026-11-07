import { useTranslation } from "react-i18next";

/** Planning does not allow adding, deleting, reordering or splitting; point to the timeline after confirmation. Shared by all three content-confirmation plans. */
export function PlanStructureHint() {
  const { t } = useTranslation("dashboard");
  return <p className="px-1 text-xs text-muted-foreground">{t("review_structure_hint")}</p>;
}
