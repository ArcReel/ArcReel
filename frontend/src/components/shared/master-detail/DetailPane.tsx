import type { ReactNode } from "react";

interface DetailPaneProps {
  header?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
}

export function DetailPane({ header, footer, children }: DetailPaneProps) {
  return (
    <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
      {header && <div className="shrink-0 border-b border-border px-6 py-4">{header}</div>}
      <div className="relative min-h-0 flex-1 overflow-y-auto [scrollbar-gutter:stable]">{children}</div>
      {footer && <div className="shrink-0 border-t border-border bg-card px-6 py-3">{footer}</div>}
    </div>
  );
}
