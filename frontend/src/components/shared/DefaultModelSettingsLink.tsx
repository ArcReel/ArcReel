import { useTranslation } from "react-i18next";
import { Link } from "wouter";

import { settingsSectionPath } from "@/app-routes";
import { buttonVariants } from "@/components/ui/button";

/** 文本任务缺少默认模型时，各失败提示共用的设置入口。 */
export function DefaultModelSettingsLink() {
  const { t } = useTranslation("workflow");
  return (
    <Link href={`~${settingsSectionPath("default-models")}`} className={buttonVariants({ variant: "outline", size: "sm" })}>
      {t("act_configure_default_model")}
    </Link>
  );
}
