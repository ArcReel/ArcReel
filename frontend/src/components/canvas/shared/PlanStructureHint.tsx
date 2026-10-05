import { useTranslation } from "react-i18next";

/** 规划上不开放增删、改序与拆分，指向确认后的时间线。内容确认页三种规划共用。 */
export function PlanStructureHint() {
  const { t } = useTranslation("dashboard");
  return <p className="px-1 text-xs text-muted-foreground">{t("review_structure_hint")}</p>;
}
