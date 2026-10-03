import type { ReactNode } from "react";

import { CARD_STYLE } from "./darkroom-tokens";

interface SectionShellProps {
  kicker: string;
  title: string;
  description?: string;
  trailing?: ReactNode;
  children: ReactNode;
}

export function SectionShell({ kicker, title, description, trailing, children }: SectionShellProps) {
  return (
    <section>
      <div className="mb-3.5 flex items-start justify-between gap-3">
        <div>
          <div className="font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-primary">
            {kicker}
          </div>
          <h3 className="mt-1 text-[14.5px] font-medium text-foreground">{title}</h3>
          {description && (
            <p className="mt-1 text-[12px] leading-[1.55] text-muted-foreground">{description}</p>
          )}
        </div>
        {trailing && <div className="shrink-0">{trailing}</div>}
      </div>
      <div className="rounded-lg border border-border p-4" style={CARD_STYLE}>
        {children}
      </div>
    </section>
  );
}
