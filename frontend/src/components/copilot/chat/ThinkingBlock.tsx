import { Brain, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";

// ---------------------------------------------------------------------------
// ThinkingBlock – 思考过程只占一行：流式期间显示「正在思考」，完成后显示「思考过程」，
// 点开看全文。只有签名、没有文字的思考块不渲染。
// ---------------------------------------------------------------------------

interface ThinkingBlockProps {
  thinking?: string;
  /** 该块正在流式生成（属于草稿的末尾块）。 */
  streaming?: boolean;
}

export function ThinkingBlock({ thinking, streaming }: ThinkingBlockProps) {
  const { t } = useTranslation("dashboard");

  if (streaming) {
    return (
      <p className="flex h-6 items-center gap-1.5 text-xs text-muted-foreground">
        <Brain aria-hidden className="size-3" />
        {t("thinking_streaming")}
        <span aria-hidden className="size-1.5 animate-breath rounded-full bg-primary" />
      </p>
    );
  }

  if (!thinking?.trim()) return null;

  return (
    <Collapsible render={<div className="text-muted-foreground" />}>
      <CollapsibleTrigger render={<Button variant="ghost" size="xs" className="-ml-2" />}>
        <Brain data-icon="inline-start" aria-hidden />
        {t("thinking_process_label")}
        <ChevronRight data-icon="inline-end" aria-hidden className="transition-transform group-aria-expanded/button:rotate-90" />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <p className="mt-1 ml-1.5 max-w-[40em] border-l border-border pl-3 text-xs/relaxed whitespace-pre-wrap">
          {thinking}
        </p>
      </CollapsibleContent>
    </Collapsible>
  );
}
