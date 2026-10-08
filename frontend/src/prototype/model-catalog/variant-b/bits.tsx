// PROTOTYPE — #3091 变体 B 的小部件与选择器。

import { cn } from "cn";
import { settingsSectionPath } from "@/app-routes";
import {
  effectiveOf,
  statusOf,
  type CatalogEntry,
  type EndpointKey,
  type EntryStatus,
  type MediaType,
  type Provider,
  type ProviderModel,
} from "../model";

/** 供应商分区内的地址：provider / custom 选中供应商，pm 打开某个供应商模型的 Sheet。 */
export function providersPath(provider: Provider, pmKey?: string): string {
  const params: Record<string, string> = provider.kind === "builtin" ? { provider: provider.id } : { custom: provider.id };
  if (pmKey) params.pm = pmKey;
  params.variant = "B";
  return settingsSectionPath("providers", params);
}

export type ModelFlag = "modified" | "outdated" | "unpriced" | "deprecated" | "user-created" | "user-created-official" | "unmapped";

/** 供应商模型在列表与 Sheet 上要提示的状态，按严重程度排列。 */
export function flagsOf(model: ProviderModel, entry: CatalogEntry | undefined): ModelFlag[] {
  if (!entry) return ["unmapped", "unpriced"];
  const flags: ModelFlag[] = [];
  const status = statusOf(entry);
  const data = effectiveOf(entry);
  if (status === "modified-outdated") flags.push("outdated");
  if (status === "modified" || status === "modified-outdated") flags.push("modified");
  if (status === "user-created") flags.push("user-created");
  if (status === "user-created-official") flags.push("user-created-official");
  if (data.deprecated) flags.push("deprecated");
  if (!data.price.meters.length) flags.push("unpriced");
  return flags;
}

const FLAG_LABEL: Record<ModelFlag, string> = {
  modified: "条目已修改",
  outdated: "官方有更新",
  unpriced: "未定价",
  deprecated: "官方已下架",
  "user-created": "用户新建条目",
  "user-created-official": "官方已收录",
  unmapped: "未对应条目",
};

const FLAG_TONE: Record<ModelFlag, string> = {
  modified: "border-primary/40 text-primary",
  outdated: "border-warn/50 text-warn",
  unpriced: "border-warn/50 text-warn",
  deprecated: "border-destructive/50 text-destructive",
  "user-created": "border-border text-subtle-foreground",
  "user-created-official": "border-good/50 text-good",
  unmapped: "border-warn/50 text-warn",
};

export function FlagBadge({ flag, className }: { flag: ModelFlag; className?: string }) {
  return (
    <span className={cn("inline-flex h-5 shrink-0 items-center rounded-full border px-1.5 text-xs whitespace-nowrap", FLAG_TONE[flag], className)}>
      {FLAG_LABEL[flag]}
    </span>
  );
}

const STATUS_TONE: Record<EntryStatus, string> = {
  factory: "border-border text-subtle-foreground",
  synced: "border-border text-subtle-foreground",
  modified: "border-primary/40 text-primary",
  "modified-outdated": "border-warn/50 text-warn",
  "user-created": "border-border text-subtle-foreground",
  "user-created-official": "border-good/50 text-good",
};
const STATUS_TEXT: Record<EntryStatus, string> = {
  factory: "跟随官方 · 出厂快照",
  synced: "跟随官方",
  modified: "已修改",
  "modified-outdated": "已修改 · 官方有更新",
  "user-created": "用户新建",
  "user-created-official": "用户新建 · 官方已收录",
};

export function StatusBadge({ status }: { status: EntryStatus }) {
  return (
    <span className={cn("inline-flex h-5 shrink-0 items-center rounded-full border px-1.5 text-xs whitespace-nowrap", STATUS_TONE[status])}>
      {STATUS_TEXT[status]}
    </span>
  );
}

/** 小号可切换的筛选 chip。 */
export function Chip({ active, onClick, children, count }: { active: boolean; onClick: () => void; children: React.ReactNode; count?: number }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
        active ? "border-primary/50 bg-primary/15 text-foreground" : "border-border text-subtle-foreground hover:bg-muted/50 hover:text-foreground",
      )}
    >
      {children}
      {count !== undefined && <span className="tabular-nums text-muted-foreground">{count}</span>}
    </button>
  );
}

/** 新加到自定义供应商的模型默认挂接的端点。 */
export const DEFAULT_ENDPOINT: Record<MediaType, EndpointKey> = {
  text: "openai-chat",
  image: "openai-images",
  video: "newapi-video",
  audio: "openai-tts",
};

export function SectionTitle({ title, hint, action }: { title: string; hint?: string; action?: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="flex min-w-0 flex-col gap-0.5">
        <h3 className="text-sm font-medium">{title}</h3>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </div>
      {action}
    </div>
  );
}

/** 字段下方的官方值标注：差异就地显示，带「采用」。 */
export function OfficialHint({ value, onAdopt }: { value: string; onAdopt?: () => void }) {
  return (
    <div className="mt-1 flex items-start gap-2 text-xs text-muted-foreground">
      <span className="min-w-0 flex-1">
        官方值：<span className="text-subtle-foreground">{value}</span>
      </span>
      {onAdopt && (
        <button type="button" onClick={onAdopt} className="shrink-0 text-primary hover:underline">
          采用
        </button>
      )}
    </div>
  );
}
