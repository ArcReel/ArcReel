import { ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Marker, MarkerContent } from "@/components/ui/marker";
import { TextBlock } from "./TextBlock";

// ---------------------------------------------------------------------------
// CompactionMarker — 上下文压缩后的续接摘要：一条「上下文已压缩」分隔线，点开看摘要。
// 摘要由 CLI 生成、以用户消息的形态注入，不是创作者写的消息，不显示为气泡。
// ---------------------------------------------------------------------------

export function CompactionMarker({ summary }: Readonly<{ summary?: string }>) {
  const { t } = useTranslation("dashboard");
  return (
    <Collapsible render={<div className="flex min-w-0 flex-col gap-2" />}>
      <Marker variant="separator">
        <MarkerContent>
          <CollapsibleTrigger render={<Button variant="ghost" size="xs" />}>
            {t("chat_compacted_label")}
            <ChevronRight
              data-icon="inline-end"
              aria-hidden
              className="transition-transform group-aria-expanded/button:rotate-90"
            />
          </CollapsibleTrigger>
        </MarkerContent>
      </Marker>
      <CollapsibleContent>
        <div className="max-w-[40em] rounded-lg bg-muted px-3 py-2 text-subtle-foreground">
          <TextBlock text={summary} />
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}
