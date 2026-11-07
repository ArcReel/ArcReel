import type { ReactNode } from "react";
import { AlertTriangle, Loader2, Lock, RefreshCcw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export type Load<T> =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: T };

/**
 * Shared code-block appearance for template/partial bodies: preserve line breaks and wrap long lines without widening the content column.
 * Unbreakable strings still scroll horizontally, so make the code block itself focusable with a focus ring.
 */
export const SOURCE_BLOCK_CLS =
  "relative overflow-x-auto rounded-md border border-border bg-muted/40 px-3.5 py-3 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap text-subtle-foreground outline-none focus-visible:ring-3 focus-visible:ring-ring/50";

/** Short code fragments such as values and enums. */
export const CHIP_CLS =
  "rounded-sm border border-border bg-muted/40 px-1.5 py-0.5 font-mono text-xs break-all text-subtle-foreground";

export function LoadingCard({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-border bg-card px-4 py-6 text-sm text-muted-foreground">
      <Loader2 aria-hidden className="size-4 text-primary animate-spin" />
      {label}
    </div>
  );
}

export function EmptyCard({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-card px-4 py-6 text-sm text-muted-foreground">{children}</div>
  );
}

export function ErrorCard({
  title,
  message,
  onRetry,
}: {
  title: string;
  message: string;
  onRetry: () => void;
}) {
  const { t } = useTranslation("common");
  return (
    <div role="alert" className="flex flex-wrap items-start gap-3 rounded-lg border border-warn/30 bg-warn/10 p-4">
      <AlertTriangle aria-hidden className="mt-0.5 size-4 shrink-0 text-warn" />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <p className="text-sm font-medium">{title}</p>
        {message && <p className="text-sm break-words text-muted-foreground">{message}</p>}
      </div>
      <Button variant="outline" size="sm" onClick={onRetry}>
        <RefreshCcw aria-hidden />
        {t("retry")}
      </Button>
    </div>
  );
}

/** Detail-page section: title/explanation outside the card and content inside it. */
export function DetailSection({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h3 className="text-sm font-medium">{title}</h3>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      <div className="min-w-0 rounded-lg border border-border bg-card p-4">{children}</div>
    </section>
  );
}

/** Locked indicator: templates/partials declaring `protected` offer no editing entry. */
export function LockBadge() {
  const { t } = useTranslation("dashboard");
  return (
    <Badge variant="outline" title={t("prompt_templates_locked_hint")}>
      <Lock aria-hidden data-icon="inline-start" />
      {t("prompt_templates_locked")}
    </Badge>
  );
}
