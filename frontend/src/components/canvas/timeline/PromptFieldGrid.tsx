import type { ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";

/**
 * 结构化提示词正文下方的可折叠参数区（构图、运镜与环境音效），默认展开。
 * 子元素是成对的「标签 + 控件」，排成标签列对齐的两列。
 */
export function PromptFieldGrid({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Collapsible defaultOpen>
      <CollapsibleTrigger render={<Button variant="ghost" size="xs" className="-ml-2" />}>
        <ChevronRight aria-hidden data-icon="inline-start" className="transition-transform group-aria-expanded/button:rotate-90" />
        {title}
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-3 gap-y-2 pt-2">{children}</div>
      </CollapsibleContent>
    </Collapsible>
  );
}
