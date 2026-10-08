// PROTOTYPE — #3091 变体 A 内部共用的小件：状态徽标、地址、简易下拉、分段控件。
import type { ReactNode } from "react";
import { useLocation, useSearch } from "wouter";
import { cn } from "cn";
import { settingsSectionPath } from "@/app-routes";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  STATUS_LABEL,
  effectiveOf,
  money,
  priceSummary,
  statusOf,
  type CatalogEntry,
  type EntryStatus,
  type PriceTable,
} from "../model";

export function useParam(name: string): string | null {
  return new URLSearchParams(useSearch()).get(name);
}

/** 变体 A 内的跳转：始终带上 variant=A，避免切换栏补参数前闪一下别的变体。 */
export function useGo() {
  const [, navigate] = useLocation();
  return (section: "models" | "providers", params: Record<string, string> = {}, replace = true) =>
    navigate(settingsSectionPath(section, { ...params, variant: "A" }), { replace });
}

export const modelsPath = (entry: string) => settingsSectionPath("models", { entry, variant: "A" });
export const providerPath = (pv: string) => settingsSectionPath("providers", { pv, variant: "A" });

const STATUS_TONE: Record<EntryStatus, string> = {
  factory: "border-border text-muted-foreground",
  synced: "border-border text-muted-foreground",
  modified: "border-primary/40 text-primary",
  "modified-outdated": "border-warn/50 text-warn",
  "user-created": "border-primary/40 text-primary",
  "user-created-official": "border-warn/50 text-warn",
};

export function StatusBadge({ entry, className }: { entry: CatalogEntry; className?: string }) {
  const status = statusOf(entry);
  return (
    <Badge variant="outline" className={cn(STATUS_TONE[status], className)}>
      {STATUS_LABEL[status]}
    </Badge>
  );
}

export function isUnpriced(entry: CatalogEntry): boolean {
  return effectiveOf(entry).price.meters.length === 0;
}

export function UnpricedBadge() {
  return (
    <Badge variant="outline" className="border-warn/50 text-warn">
      未定价
    </Badge>
  );
}

/** 价格摘要乘上倍率；倍率为 0 直接记 0，不查价（ADR 0099）。 */
export function scaledSummary(price: PriceTable, multiplier: number): string {
  if (multiplier === 0) return `${money(0, price.currency)}（倍率 0）`;
  if (!price.meters.length) return "未定价";
  if (multiplier === 1) return priceSummary(price);
  const scaled: PriceTable = {
    ...price,
    meters: price.meters.map((m) => ({ ...m, rows: m.rows.map((r) => ({ ...r, price: r.price * multiplier })) })),
  };
  return `${priceSummary(scaled)}（×${multiplier}）`;
}

export interface Option {
  value: string;
  label: string;
}

export function SimpleSelect({
  value,
  options,
  onChange,
  label,
  className,
  disabled,
}: {
  value: string;
  options: Option[];
  onChange: (v: string) => void;
  label: string;
  className?: string;
  disabled?: boolean;
}) {
  return (
    <Select items={options} value={value} onValueChange={(v) => v !== null && onChange(String(v))} disabled={disabled}>
      <SelectTrigger aria-label={label} className={className} size="sm">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((o) => (
          <SelectItem key={o.value} value={o.value}>
            {o.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex h-8 shrink-0 items-center rounded-lg bg-muted p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={o.value === value}
          onClick={() => onChange(o.value)}
          className={cn(
            "h-7 rounded-md px-2.5 text-[0.8rem] whitespace-nowrap text-muted-foreground transition-colors hover:text-foreground",
            o.value === value && "bg-background text-foreground shadow-sm",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Section({ title, hint, action, children }: { title: string; hint?: ReactNode; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-end gap-3">
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <h3 className="text-base font-medium">{title}</h3>
          {hint && <p className="text-sm text-muted-foreground">{hint}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export function FieldLabel({ children, htmlFor }: { children: ReactNode; htmlFor?: string }) {
  return (
    <label htmlFor={htmlFor} className="text-sm font-medium">
      {children}
    </label>
  );
}
