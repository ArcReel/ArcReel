import { Brain, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";

// ---------------------------------------------------------------------------
// ThinkingBlock: thinking occupies one row, showing Thinking while streaming and Thought process after completion;
// expand to read the full text. Do not render thinking blocks with signatures but no text.
// ---------------------------------------------------------------------------

interface ThinkingBlockProps {
  thinking?: string;
  /** This block is streaming, as the draft's last block. */
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
