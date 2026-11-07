import { useId, type ReactNode } from "react";
import { cn } from "cn";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { PreviewedRequest } from "@/types";

/** One endpoint-test card. `badge` indicates costs such as one billable call or GPU occupancy. */
export function TestCard({
  title,
  badge,
  desc,
  children,
}: {
  title: string;
  badge?: string;
  desc: string;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border p-4">
      <div className="flex flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <h4 className="text-sm font-medium">{title}</h4>
          {badge && <Badge variant="outline">{badge}</Badge>}
        </div>
        <p className="max-w-[40em] text-xs text-muted-foreground">{desc}</p>
      </div>
      {children}
    </section>
  );
}

/** One endpoint-test field: label above control, with `htmlFor` pointing to its ID. */
export function TestField({
  label,
  htmlFor,
  className,
  children,
}: {
  label: ReactNode;
  htmlFor?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-1.5", className)}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
    </div>
  );
}

export interface TestSelectItem {
  value: string;
  label: string;
  disabled?: boolean;
}

/** Endpoint-test dropdown: associate label and trigger with `aria-labelledby`, allowing screen readers and tests to find it by label. */
export function TestSelect({
  label,
  value,
  items,
  onValueChange,
  className,
}: {
  label: string;
  value: string;
  items: TestSelectItem[];
  onValueChange: (value: string) => void;
  className?: string;
}) {
  const labelId = useId();
  return (
    <div className={cn("flex min-w-0 flex-col gap-1.5", className)}>
      <span id={labelId} className="text-sm font-medium">
        {label}
      </span>
      <Select
        items={items}
        value={value}
        onValueChange={(next) => {
          if (typeof next === "string") onValueChange(next);
        }}
      >
        <SelectTrigger aria-labelledby={labelId} className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {items.map((item) => (
            <SelectItem key={item.value} value={item.value} disabled={item.disabled}>
              {item.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

/** One rendered, redacted request section, laid out line by line as actually sent. Long URLs/bodies scroll within their frames without widening the page. */
export function RequestPreview({ label, request }: { label: string; request: PreviewedRequest }) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <span className="text-sm font-medium">{label}</span>
      <pre className="relative max-h-96 overflow-auto rounded-lg border border-border bg-muted/30 p-3 font-mono text-xs text-subtle-foreground">
        {`${request.method} ${request.url}\n`}
        {Object.entries(request.headers)
          .map(([k, v]) => `${k}: ${v}`)
          .join("\n")}
        {request.body === null || request.body === undefined
          ? ""
          : `\n\n${JSON.stringify(request.body, null, 2)}`}
      </pre>
    </div>
  );
}
