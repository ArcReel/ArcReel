import { useTranslation } from "react-i18next";
import { ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";

function paramsText(params: Record<string, unknown> | undefined): string {
  return Object.entries(params ?? {})
    .map(([key, value]) => `${key}=${typeof value === "object" && value !== null ? JSON.stringify(value) : String(value)}`)
    .join(", ");
}

export function TaskFailureDetails({
  code,
  params,
  detail,
}: {
  code: string | null | undefined;
  params: Record<string, unknown> | undefined;
  detail: string | null | undefined;
}) {
  const { t } = useTranslation("dashboard");
  if (!detail) return null;
  return (
    <Collapsible render={<div className="flex w-full min-w-0 flex-col gap-1" />}>
      <CollapsibleTrigger render={<Button variant="ghost" size="xs" />}>
        {t("text_task_failure_details")}
        <ChevronRight
          data-icon="inline-end"
          aria-hidden
          className="transition-transform group-aria-expanded/button:rotate-90"
        />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs text-subtle-foreground">
          <dt>{t("text_task_failure_code")}</dt>
          <dd className="m-0 min-w-0 font-mono break-all">{code}</dd>
          <dt>{t("text_task_failure_params")}</dt>
          <dd className="m-0 min-w-0 font-mono break-all">{paramsText(params)}</dd>
          <dt>{t("text_task_failure_server_text")}</dt>
          <dd className="m-0 min-w-0 font-mono break-all whitespace-pre-wrap">{detail}</dd>
        </dl>
      </CollapsibleContent>
    </Collapsible>
  );
}
