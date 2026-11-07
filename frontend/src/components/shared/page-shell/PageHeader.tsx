import type { ReactNode } from "react";
import { ChevronLeft } from "lucide-react";

import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";

interface PageHeaderProps {
  /** Back button; use `useReturnTo` for `onClick` when returning to the location before entering this page. */
  back?: { label: string; onClick: () => void };
  title?: string;
  subtitle?: string;
  /** Custom content after the title group, such as lobby branding and search. */
  children?: ReactNode;
  /** Right-aligned page-level actions. */
  actions?: ReactNode;
}

/**
 * Page-shell header, 56px high: Back | Title | Subtitle grouped left, with the back arrow vertically aligned to the sidebar icon,
 * and page actions aligned right.
 */
export function PageHeader({ back, title, subtitle, children, actions }: PageHeaderProps) {
  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border pr-6 pl-3 xl:pr-8">
      <div className="flex min-w-0 flex-1 items-center gap-3">
        {back && (
          <>
            <Button variant="ghost" onClick={back.onClick}>
              <ChevronLeft aria-hidden data-icon="inline-start" />
              {back.label}
            </Button>
            <span aria-hidden className="h-4 w-px shrink-0 bg-border" />
          </>
        )}
        {title && (
          <h1 className="min-w-0 text-base font-medium">
            <TruncatedText text={title} />
          </h1>
        )}
        {subtitle && <TruncatedText text={subtitle} className="text-sm text-muted-foreground" />}
        {children}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  );
}
