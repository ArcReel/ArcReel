import { useTranslation } from "react-i18next";
import { Link } from "wouter";

import { buttonVariants } from "@/components/ui/button";
import { customModelSettingsPath, type OutputTruncation } from "@/utils/output-truncation";

/**
 * Recovery from truncated text-task output: for custom-provider models, link to the model's settings entry to register its maximum output length;
 * for built-in models, suggest switching text models. Failure feedback for all text tasks shares this component.
 */
export function OutputTruncationHint({ truncation }: { truncation: OutputTruncation }) {
  const { t } = useTranslation("dashboard");
  if (!truncation.custom) return <p className="m-0 text-muted-foreground">{t("text_output_truncated_switch_model")}</p>;
  const settingsPath = customModelSettingsPath(truncation.providerId, truncation.model);
  if (settingsPath === null) return null;
  return (
    <Link href={`~${settingsPath}`} className={buttonVariants({ variant: "outline", size: "sm" })}>
      {t("text_output_truncated_register_limit")}
    </Link>
  );
}
