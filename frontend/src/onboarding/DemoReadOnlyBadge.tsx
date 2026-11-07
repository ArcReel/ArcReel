import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/**
 * The “Demo · Read-only” badge beside the project title in the demo workspace header explains on hover or focus that all writes are unavailable.
 * The explanation is also sr-only text inside the badge, so screen readers do not depend on the tooltip. Disabled buttons still individually explain “Unavailable in demo” in place.
 */
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
