import type { ReactNode } from "react";

interface DetailPaneProps {
  /** Headers: names, states and header actions, outside body scrolling. */
  header?: ReactNode;
  /** Footers: persistent save bars (`SaveBar`), outside body scrolling. */
  footer?: ReactNode;
  children: ReactNode;
}

/**
 * Full-bleed master/detail columns, alongside `SecondaryRail` in section roots (`flex min-h-0 flex-1`).
 * Three regions, tab headers/body/footer: only bodies scroll; save bars stay fixed at detail bottoms outside scroll regions.
 * Bodies have no padding/width caps; callers choose by contents (forms usually `max-w-190 px-6 py-6`);
 * width-limited bodies also constrain footer contents (`max-w-178`), aligning save bars with forms rather than entire columns.
 * On selection changes, callers pass `key` to rebuild details entirely, preventing previous unsaved edits/pending requests/loading states leaking forward.
 */
export function DetailPane({ header, footer, children }: DetailPaneProps) {
  return (
    <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
      {header && <div className="shrink-0 border-b border-border px-6 py-4">{header}</div>}
      <div className="relative min-h-0 flex-1 overflow-y-auto [scrollbar-gutter:stable]">{children}</div>
      {footer && <div className="shrink-0 border-t border-border bg-card px-6 py-3">{footer}</div>}
    </div>
  );
}
