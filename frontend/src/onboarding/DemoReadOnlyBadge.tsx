import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export function DemoReadOnlyBadge() {
  const { t } = useTranslation("onboarding");
  const hint = t("demo_badge_hint");

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Badge
            variant="outline"
            tabIndex={0}
          />
        }
      >
        {t("demo_badge")}
        <span className="sr-only">{hint}</span>
      </TooltipTrigger>
      <TooltipContent side="bottom">
        {hint}
      </TooltipContent>
    </Tooltip>
  );
}
