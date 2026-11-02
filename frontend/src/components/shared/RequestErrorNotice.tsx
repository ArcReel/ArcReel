import { ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import { ApiRequestError } from "@/api";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { errMsg } from "@/utils/async";

/** 诊断里可以展示给使用者的原文：供应商错误码与原始消息，其余字段不展示。 */
function diagnosticText(error: unknown): string | null {
  if (!(error instanceof ApiRequestError)) return null;
  const raw = error.diagnostic;
  if (typeof raw !== "object" || raw === null) return null;
  const { code, message } = raw as { code?: unknown; message?: unknown };
  const parts = [code, message].filter((part): part is string => typeof part === "string" && part.length > 0);
  return parts.length > 0 ? parts.join(": ") : null;
}

/**
 * 请求失败提示：摘要是服务端给出的本地化原因与下一步，供应商原文折叠在「详情」里。
 */
export function RequestErrorNotice({
  error,
  action,
  className,
}: {
  error: unknown;
  /** 摘要旁的恢复动作，如「重试」。 */
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
