import type { ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";

/**
 * Collapsible parameter area below structured prompt text (composition, camera movement and environmental audio), expanded by default.
 * Children are label/control pairs arranged into two columns with aligned label columns.
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
