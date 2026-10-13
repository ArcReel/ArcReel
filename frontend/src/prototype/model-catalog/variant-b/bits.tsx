// PROTOTYPE — #3091 变体 B 的小部件与选择器。

import { cn } from "cn";
import { settingsSectionPath } from "@/app-routes";
import {
  ENDPOINTS,
  MEDIA_LABEL,
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

export type ModelFlag = "modified" | "unpriced" | "deprecated" | "user-created" | "unmapped";

/** 供应商模型在列表与 Sheet 上要提示的状态，按严重程度排列。 */
export function flagsOf(_model: ProviderModel, entry: CatalogEntry | undefined): ModelFlag[] {
  if (!entry) return ["unmapped"];
  const flags: ModelFlag[] = [];
  const status = statusOf(entry);
  const data = effectiveOf(entry);
  if (status === "modified" || status === "modified-outdated") flags.push("modified");
  if (status === "user-created" || status === "user-created-official") flags.push("user-created");
  if (data.deprecated) flags.push("deprecated");
  if (!data.price.meters.length) flags.push("unpriced");
  return flags;
}

const FLAG_LABEL: Record<ModelFlag, string> = {
  modified: "条目已修改",
  unpriced: "未定价",
  deprecated: "已下架",
  "user-created": "自建条目",
  unmapped: "未对应条目",
};

const FLAG_TONE: Record<ModelFlag, string> = {
  modified: "border-primary/40 text-primary",
  unpriced: "border-warn/50 text-warn",
  deprecated: "border-destructive/50 text-destructive",
  "user-created": "border-border text-subtle-foreground",
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
  "modified-outdated": "border-primary/40 text-primary",
  "user-created": "border-border text-subtle-foreground",
  "user-created-official": "border-border text-subtle-foreground",
};
const STATUS_TEXT: Record<EntryStatus, string> = {
  factory: "跟随官方",
  synced: "跟随官方",
  modified: "已修改",
  "modified-outdated": "已修改",
  "user-created": "自建",
  "user-created-official": "自建",
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

/** 自定义供应商新加模型时挑端点：沿用这个供应商同媒体类型用得最多的端点，并列时优先默认端点；一个都没有就用默认。 */
export function endpointFor(provider: Provider, media: MediaType, entries: CatalogEntry[]): EndpointKey {
  const counts = new Map<EndpointKey, number>();
  for (const m of provider.models) {
    const e = entries.find((x) => x.id === m.entryId);
    if (m.endpoint && e && effectiveOf(e).mediaType === media) counts.set(m.endpoint, (counts.get(m.endpoint) ?? 0) + 1);
  }
  const fallback = DEFAULT_ENDPOINT[media];
  let best: EndpointKey = fallback;
  let bestCount = counts.get(fallback) ?? 0;
  for (const [ep, n] of counts) if (n > bestCount) [best, bestCount] = [ep, n];
  return best;
}

/** 端点名本身不含媒体类型时补上，如「OpenAI Chat（文本）」；「new-api 视频」不再重复。 */
export function endpointLabel(key: EndpointKey): string {
  const e = ENDPOINTS[key];
  const media = MEDIA_LABEL[e.mediaType];
  return e.label.includes(media) ? e.label : `${e.label}（${media}）`;
}

const norm = (s: string) => s.toLowerCase().replace(/^[^/]+\//, "").replace(/\./g, "-");

/**
 * 按 API 模型名找对应的条目：去掉「厂商/」前缀、点号视同连字符后，与官方模型名或别名相同，
 * 或者两者只差一段日期后缀（如 claude-sonnet-4-5-20250929、doubao-seedance-2-0-260128）。
 */
export function matchEntry(entries: CatalogEntry[], apiName: string): CatalogEntry | undefined {
  const n = norm(apiName);
  const dated = (long: string, short: string) => long.startsWith(`${short}-`) && /^\d{6,8}$/.test(long.slice(short.length + 1));
  return entries.find((e) => {
    const d = effectiveOf(e);
    return [d.name, ...(d.aliases ?? [])].map(norm).some((c) => c === n || dated(n, c) || dated(c, n));
  });
}

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

/** 字段下方的官方值标注：用户维护的条目与官方数据不同时就地显示，带「采用」。 */
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
