import { useTranslation } from "react-i18next";
import { Link } from "wouter";

import { settingsSectionPath } from "@/app-routes";
import { buttonVariants } from "@/components/ui/button";

export function DefaultModelSettingsLink() {
  const { t } = useTranslation("workflow");
  return (
    <Link href={`~${settingsSectionPath("default-models")}`} className={buttonVariants({ variant: "outline", size: "sm" })}>
      {t("act_configure_default_model")}
    </Link>
  );
}
