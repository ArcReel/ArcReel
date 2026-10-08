// PROTOTYPE — #3091 变体 C 内部共用的小件：选择器、状态标记、渠道小标、参考单价。
import { ArrowUpCircle, Archive, CircleDashed, Pencil, UserRound } from "lucide-react";
import { useLocation, useSearch } from "wouter";
import { ProviderIcon } from "@/components/shared/ProviderIcon";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "cn";
import {
  METER_LABEL,
  UNIT_SHORT,
  effectiveOf,
  money,
  statusOf,
  type CatalogEntry,
  type EntryData,
  type Provider,
  type ProviderModel,
} from "../model";

// ---------- 地址参数 ----------

/** 变体 C 的定位参数都放在地址里：entry（条目）、orphan（未对应条目的供应商模型）、p（供应商）。 */
export function useParams(): [URLSearchParams, (patch: Record<string, string | null>) => void] {
  const search = useSearch();
  const [location, navigate] = useLocation();
  const params = new URLSearchParams(search);
  const update = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(search);
    for (const [k, v] of Object.entries(patch)) {
      if (v === null) next.delete(k);
      else next.set(k, v);
    }
    next.set("variant", "C");
    navigate(`${location}?${next.toString()}`, { replace: true });
  };
  return [params, update];
}

// ---------- 下拉 ----------

export function SimpleSelect<T extends string>({
  value,
  onChange,
  items,
  placeholder,
  className,
  id,
  disabled,
  size,
}: {
  value: T | null;
  onChange: (v: T) => void;
  items: { value: T; label: string; hint?: string }[];
  placeholder?: string;
  className?: string;
  id?: string;
  disabled?: boolean;
  size?: "sm" | "default";
}) {
  const selected = items.find((i) => i.value === value);
  return (
    <Select<T> value={value} onValueChange={(v) => v !== null && onChange(v)} disabled={disabled}>
      <SelectTrigger id={id} size={size} className={cn("w-full", className)}>
        <SelectValue>
          {() => (selected ? <span className="truncate">{selected.label}</span> : <span className="truncate text-muted-foreground">{placeholder ?? "请选择"}</span>)}
        </SelectValue>
      </SelectTrigger>
      <SelectContent alignItemWithTrigger={false}>
        {items.map((i) => (
          <SelectItem key={i.value} value={i.value}>
            <span className="flex min-w-0 items-baseline gap-2">
              <span className="truncate">{i.label}</span>
              {i.hint && <span className="truncate text-xs text-muted-foreground">{i.hint}</span>}
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

// ---------- 状态标记 ----------

export type ListFlag = "modified" | "outdated" | "user" | "unpriced" | "deprecated";
export const FLAG_LABEL: Record<ListFlag, string> = {
  modified: "已修改",
  outdated: "官方有更新",
  user: "用户新建",
  unpriced: "未定价",
  deprecated: "官方已下架",
};

export function flagsOf(e: CatalogEntry): ListFlag[] {
  const s = statusOf(e);
  const data = effectiveOf(e);
  const out: ListFlag[] = [];
  if (s === "modified") out.push("modified");
  if (s === "modified-outdated") out.push("modified", "outdated");
  if (s === "user-created") out.push("user");
  if (s === "user-created-official") out.push("user", "outdated");
  if (!data.price.meters.length) out.push("unpriced");
  if (data.deprecated) out.push("deprecated");
  return out;
}

const FLAG_ICON: Record<ListFlag, { Icon: typeof Pencil; cls: string }> = {
  modified: { Icon: Pencil, cls: "text-warn" },
  outdated: { Icon: ArrowUpCircle, cls: "text-primary" },
  user: { Icon: UserRound, cls: "text-subtle-foreground" },
  unpriced: { Icon: CircleDashed, cls: "text-muted-foreground" },
  deprecated: { Icon: Archive, cls: "text-muted-foreground" },
};

export function FlagIcon({ flag, className }: { flag: ListFlag; className?: string }) {
  const { Icon, cls } = FLAG_ICON[flag];
  return (
    <span role="img" aria-label={FLAG_LABEL[flag]} title={FLAG_LABEL[flag]} className={cn("inline-flex", cls, className)}>
      <Icon aria-hidden className="size-3.5" />
    </span>
  );
}

// ---------- 渠道小标 ----------

export function ProviderGlyph({ provider, className }: { provider: Provider; className?: string }) {
  if (provider.kind === "builtin") return <ProviderIcon providerId={provider.id} className={cn("size-3.5", className)} />;
  return (
    <span className={cn("inline-flex size-3.5 items-center justify-center rounded-[3px] bg-muted text-[10px] leading-none text-subtle-foreground", className)}>
      {Array.from(provider.displayName)[0] ?? "?"}
    </span>
  );
}

/** 一个渠道一枚小标：已启用实心，未启用描边。 */
export function ChannelChip({ provider, model }: { provider: Provider; model: ProviderModel }) {
  const on = model.enabled && provider.credentialCount > 0;
  return (
    <span
      title={`${provider.displayName}${on ? "（已启用）" : "（未启用）"}`}
      className={cn(
        "inline-flex size-5 items-center justify-center rounded-md",
        on ? "bg-muted ring-1 ring-foreground/15" : "border border-dashed border-border opacity-50",
      )}
    >
      <ProviderGlyph provider={provider} />
    </span>
  );
}

// ---------- 参考单价 ----------

const PRIMARY_METERS = ["input_tokens", "output_images", "output_seconds", "video_tokens", "characters", "requests"] as const;

/** 条目的主要单价乘上渠道倍率，如「$2.4 /M 输入」「¥0.48 起 /秒」。 */
export function channelPrice(data: EntryData, multiplier: number): string {
  if (multiplier === 0) return "0（倍率为 0）";
  const meter = data.price.meters.find((m) => (PRIMARY_METERS as readonly string[]).includes(m.meter));
  if (!meter) return "未定价";
  const prices = meter.rows.map((r) => r.price);
  const lo = Math.min(...prices) * multiplier;
  const from = new Set(prices).size > 1 ? " 起" : "";
  const label = meter.meter === "input_tokens" ? " 输入" : "";
  return `${money(lo, data.price.currency)}${from} ${UNIT_SHORT[meter.unit]}${label}`;
}

export function meterName(key: keyof typeof METER_LABEL): string {
  return METER_LABEL[key];
}

export function SectionHeading({ title, hint, action }: { title: string; hint?: string; action?: React.ReactNode }) {
  return (
    <div className="flex items-end justify-between gap-4">
      <div className="flex min-w-0 flex-col gap-1">
        <h3 className="text-base font-medium">{title}</h3>
        {hint && <p className="max-w-[40em] text-sm text-muted-foreground">{hint}</p>}
      </div>
      {action && <div className="flex shrink-0 items-center gap-2">{action}</div>}
    </div>
  );
}
