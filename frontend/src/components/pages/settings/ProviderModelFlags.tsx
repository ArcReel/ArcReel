import { useTranslation } from "react-i18next";
import { cn } from "cn";
import type { ProviderModelFlag } from "@/types";

const FLAG_LABEL_KEY: Record<ProviderModelFlag, string> = {
  discontinued: "pm_flag_discontinued",
  retiring: "pm_flag_retiring",
  unpriced: "pm_flag_unpriced",
  modified: "pm_flag_modified",
};

const FLAG_TONE: Record<ProviderModelFlag, string> = {
  discontinued: "border-destructive/50 text-destructive",
  retiring: "border-warn/50 text-warn",
  unpriced: "border-warn/50 text-warn",
  modified: "border-border text-muted-foreground",
};

/**
 * Model status markers (retiring, retired, unpriced, modified), shared by list rows and Sheet headings.
 * Retiring states show the shutdown date from `shutdownDate`.
 */
export function ProviderModelFlags({
  flags,
  shutdownDate = null,
}: {
  flags: ProviderModelFlag[];
  shutdownDate?: string | null;
}) {
  const { t } = useTranslation("dashboard");
  return flags.map((flag) => (
    <span
      key={flag}
      className={cn(
        "inline-flex h-5 shrink-0 items-center rounded-full border px-1.5 text-xs font-normal whitespace-nowrap",
        FLAG_TONE[flag],
      )}
    >
      {t(FLAG_LABEL_KEY[flag], { date: shutdownDate ?? "" })}
    </span>
  ));
}
