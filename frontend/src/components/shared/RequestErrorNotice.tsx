import { ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import { ApiRequestError } from "@/api";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { errMsg } from "@/utils/async";

/** Raw diagnostics safe to display: provider error code and original message only; hide other fields. */
function diagnosticText(error: unknown): string | null {
  if (!(error instanceof ApiRequestError)) return null;
  const raw = error.diagnostic;
  if (typeof raw !== "object" || raw === null) return null;
  const { code, message } = raw as { code?: unknown; message?: unknown };
  const parts = [code, message].filter((part): part is string => typeof part === "string" && part.length > 0);
  return parts.length > 0 ? parts.join(": ") : null;
}

/**
 * Request-failure notice: the summary uses the server's localized reason and next step, with original provider text collapsed under Details.
 */
export function RequestErrorNotice({
  error,
  action,
  className,
}: {
  error: unknown;
  /** Recovery action beside the summary, such as Retry. */
  action?: { label: string; onClick: () => void };
  className?: string;
}) {
  const { t } = useTranslation("common");
  const detail = diagnosticText(error);
  return (
    <div role="alert" className={cn("flex flex-col gap-1 text-sm text-warn", className)}>
      <div className="flex items-center gap-3">
        <span className="min-w-0 flex-1 wrap-break-word">{errMsg(error)}</span>
        {action && (
          <Button variant="outline" size="sm" onClick={action.onClick}>
            {action.label}
          </Button>
        )}
      </div>
      {detail && (
        <Collapsible>
          <CollapsibleTrigger render={<Button variant="ghost" size="xs" className="-ml-2" />}>
            <ChevronRight aria-hidden className="transition-transform group-aria-expanded/button:rotate-90" />
            {t("error_details")}
          </CollapsibleTrigger>
          <CollapsibleContent>
            <p className="font-mono text-xs wrap-break-word text-muted-foreground" translate="no">
              {detail}
            </p>
          </CollapsibleContent>
        </Collapsible>
      )}
    </div>
  );
}
