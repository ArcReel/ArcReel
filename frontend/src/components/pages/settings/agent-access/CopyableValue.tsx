import { useEffect, useId, useState } from "react";
import { Check, Copy } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import { Button } from "@/components/ui/button";
import { useAppStore } from "@/stores/app-store";
import { copyText } from "@/utils/clipboard";

const COPIED_RESET_MS = 2000;

interface CopyableValueProps {
  /** Visible labels above values; omitted labels let copyLabel alone describe buttons. */
  label?: string;
  value: string;
  /** Copy-button accessible names identify copied objects, for example Copy installation command. */
  copyLabel: string;
  /** Long text such as prompts wraps by lines; default single lines break within words. */
  multiline?: boolean;
  className?: string;
}

/**
 * Readonly commands, addresses or tokens with adjacent copy buttons.
 * Successful copies briefly show Copied and announce status; unavailable clipboards notify errors while contents stay selectable for manual copying.
 */
export function CopyableValue({ label, value, copyLabel, multiline = false, className }: CopyableValueProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [copied, setCopied] = useState(false);
  const labelId = useId();

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), COPIED_RESET_MS);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const handleCopy = () => {
    void copyText(value).then(
      () => setCopied(true),
      () => {
        setCopied(false);
        useAppStore.getState().pushToast(t("dashboard:access_copy_failed"), "error");
      },
    );
  };

  return (
    <div role="group" aria-labelledby={label ? labelId : undefined} className={cn("flex flex-col gap-1.5", className)}>
      {label && (
        <span id={labelId} className="text-xs font-medium text-muted-foreground">
          {label}
        </span>
      )}
      <div className="flex items-start gap-2 rounded-lg border border-border bg-background py-1.5 pr-1.5 pl-3">
        <code
          translate="no"
          className={cn(
            "min-w-0 flex-1 self-center font-mono text-xs/5 text-foreground",
            multiline ? "whitespace-pre-wrap wrap-break-word" : "break-all",
          )}
        >
          {value}
        </code>
        <Button
          variant="ghost"
          size="sm"
          onClick={handleCopy}
          // Remove aria-label while Copied displays, aligning accessible/visible names (WCAG 2.5.3).
          aria-label={copied ? undefined : copyLabel}
          className="shrink-0"
        >
          {copied ? <Check aria-hidden data-icon="inline-start" /> : <Copy aria-hidden data-icon="inline-start" />}
          {copied ? t("common:copied") : t("common:copy")}
        </Button>
      </div>
      <span role="status" className="sr-only">
        {copied ? t("common:copied") : ""}
      </span>
    </div>
  );
}
