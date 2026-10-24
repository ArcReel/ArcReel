import { useTranslation } from "react-i18next";
import { cn } from "cn";
import type { ProviderModelFlag } from "@/types";

const FLAG_LABEL_KEY: Record<ProviderModelFlag, string> = {
  discontinued: "pm_flag_discontinued",
  unpriced: "pm_flag_unpriced",
  modified: "pm_flag_modified",
};

const FLAG_TONE: Record<ProviderModelFlag, string> = {
  discontinued: "border-destructive/50 text-destructive",
  unpriced: "border-warn/50 text-warn",
  modified: "border-border text-muted-foreground",
};

/** 模型的状态标记（已下架、未定价、已修改），列表行与 Sheet 标题共用。 */
export function ProviderModelFlags({ flags }: { flags: ProviderModelFlag[] }) {
  const { t } = useTranslation("dashboard");
  return flags.map((flag) => (
    <span
      key={flag}
      className={cn(
        "inline-flex h-5 shrink-0 items-center rounded-full border px-1.5 text-xs font-normal whitespace-nowrap",
        FLAG_TONE[flag],
      )}
    >
      {t(FLAG_LABEL_KEY[flag])}
    </span>
  ));
}
