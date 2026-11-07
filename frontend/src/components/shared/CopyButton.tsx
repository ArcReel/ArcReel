import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { copyText } from "@/utils/clipboard";

interface CopyButtonProps {
  text: string;
  /** Override the button's accessible name before copying. */
  label?: string;
  /** Override the button's accessible name after successful copying. */
  copiedLabel?: string;
  className?: string;
}

/** Icon button inherits its row's color; successful copying briefly changes the icon to a brand-color checkmark. */
export function CopyButton({ text, label, copiedLabel, className }: CopyButtonProps) {
  const { t } = useTranslation("dashboard");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(timer);
  }, [copied]);

  const title = copied
    ? (copiedLabel ?? t("message_copied"))
    : (label ?? t("message_copy"));

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            variant="ghost"
            size="icon-xs"
            className={className}
            aria-label={title}
            onClick={() => {
              // Show the checkmark only on success: insecure contexts fall back to execCommand; do not falsely report success if that also fails
              void copyText(text).then(
                () => setCopied(true),
                () => undefined,
              );
            }}
          />
        }
      >
        {copied ? <Check aria-hidden className="text-primary" /> : <Copy aria-hidden />}
      </TooltipTrigger>
      <TooltipContent>{title}</TooltipContent>
    </Tooltip>
  );
}
