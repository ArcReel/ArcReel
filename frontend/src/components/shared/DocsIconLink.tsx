import { BookOpen } from "lucide-react";
import { useTranslation } from "react-i18next";
import { getDocsUrl } from "@/branding";
import { buttonVariants } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

interface DocsIconLinkProps {
  tooltipSide?: "top" | "bottom";
}

export function DocsIconLink({ tooltipSide }: DocsIconLinkProps) {
  const { t, i18n } = useTranslation("common");
  const label = t("documentation");

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <a
            href={getDocsUrl(i18n.language)}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={label}
            className={buttonVariants({ variant: "ghost", size: "icon" })}
          >
            <BookOpen aria-hidden />
          </a>
        }
      />
      <TooltipContent side={tooltipSide}>{label}</TooltipContent>
    </Tooltip>
  );
}
