// PROTOTYPE — #3091 变体 A：供应商模型上的渠道编辑件——能力覆盖、路径覆盖（请求表格 + 完整 URL 预览）、条目选择。
import { useMemo, useState } from "react";
import { Check, ChevronsUpDown, Search } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Switch } from "@/components/ui/switch";
import {
  ENDPOINTS,
  INPUT_MODE_LABEL,
  MEDIA_LABEL,
  channelVideoCaps,
  effectiveOf,
  makerLabel,
  previewUrl,
  type CapabilityOverride,
  type EndpointKey,
  type EntryData,
  type MediaType,
} from "../model";
import { useProto } from "../store";

// ---------- 条目选择 ----------

export function EntryPicker({ value, onChange, media }: { value: string | null; onChange: (id: string | null) => void; media?: MediaType }) {
  const entries = useProto((s) => s.entries);
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const current = entries.find((e) => e.id === value);
  const list = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return entries
      .map((e) => ({ e, d: effectiveOf(e) }))
      .filter(({ e, d }) => (!media || d.mediaType === media || !value) && (!needle || [d.displayName, e.id, ...(d.aliases ?? [])].some((s) => s.toLowerCase().includes(needle))))
      .slice(0, 60);
  }, [entries, q, media, value]);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button variant="outline" className="h-8 w-full justify-between font-normal">
            {current ? (
              <span className="min-w-0 truncate">
                {effectiveOf(current).displayName}
                <span className="ml-2 font-mono text-xs text-subtle-foreground">{current.id}</span>
              </span>
            ) : (
              <span className="text-warn">未对应条目</span>
            )}
            <ChevronsUpDown className="text-muted-foreground" />
          </Button>
        }
      />
      <PopoverContent align="start" className="w-[28rem] gap-2 p-2">
        <InputGroup>
          <InputGroupAddon>
            <Search />
          </InputGroupAddon>
          <InputGroupInput autoFocus aria-label="搜索模型目录" placeholder="搜索模型目录" value={q} onChange={(e) => setQ(e.target.value)} />
        </InputGroup>
        <ul className="flex max-h-72 flex-col overflow-y-auto">
          <li>
            <button type="button" className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted" onClick={() => (onChange(null), setOpen(false))}>
              <span className="flex-1 text-warn">不对应任何条目（调用记为未定价）</span>
              {!value && <Check className="size-4" />}
            </button>
          </li>
          {list.map(({ e, d }) => (
            <li key={e.id}>
              <button type="button" className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted" onClick={() => (onChange(e.id), setOpen(false))}>
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{d.displayName}</span>
                  <span className="block truncate font-mono text-xs text-subtle-foreground">{e.id}</span>
                </span>
                <span className="text-xs text-muted-foreground">
                  {makerLabel(d.maker)} · {MEDIA_LABEL[d.mediaType]}
                </span>
                {e.id === value && <Check className="size-4" />}
              </button>
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  );
}

// ---------- 能力覆盖 ----------

type Dim = "durations" | "resolutions" | "inputModes" | "maxReferenceImages";
const DIMS: { key: Dim; label: string; from: "entry" | "channel" }[] = [
  { key: "durations", label: "可选时长", from: "entry" },
  { key: "resolutions", label: "分辨率", from: "entry" },
  { key: "inputModes", label: "输入模式", from: "channel" },
  { key: "maxReferenceImages", label: "参考图上限", from: "channel" },
];

function show(dim: Dim, v: CapabilityOverride[Dim] | undefined): string {
  if (v === undefined) return "未声明";
  if (dim === "durations") return (v as number[]).length ? `${(v as number[]).join("、")} 秒` : "无";
  if (dim === "resolutions") return (v as string[]).join("、") || "无";
  if (dim === "inputModes") return (v as (keyof typeof INPUT_MODE_LABEL)[]).map((m) => INPUT_MODE_LABEL[m]).join("、");
  return `${v} 张`;
}

export function CapabilityOverrideEditor({
  entry,
  endpoint,
  value,
  onChange,
}: {
  entry: EntryData | null;
  endpoint: EndpointKey | undefined;
  value: CapabilityOverride | undefined;
  onChange: (next: CapabilityOverride) => void;
}) {
  const ov = value ?? {};
  const channel = channelVideoCaps(endpoint);
  const inherited: CapabilityOverride = { durations: entry?.durations, resolutions: entry?.resolutions, ...channel };
  const setDim = (dim: Dim, v: CapabilityOverride[Dim] | undefined) => {
    const next = { ...ov };
    if (v === undefined) delete next[dim];
    else (next as Record<Dim, unknown>)[dim] = v;
    onChange(next);
  };

  return (
    <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
      {DIMS.map(({ key, label, from }) => {
        const on = ov[key] !== undefined;
        return (
          <div key={key} className="flex min-h-11 items-center gap-3 px-3 py-1.5 text-sm">
            <span className="w-20 shrink-0">{label}</span>
            <div className="min-w-0 flex-1">
              {on ? (
                <OverrideInput dim={key} value={ov[key]} onChange={(v) => setDim(key, v)} />
              ) : (
                <span className="text-muted-foreground">
                  {show(key, inherited[key])}
                  <span className="ml-2 text-xs text-subtle-foreground">{from === "entry" ? "来自条目" : `来自端点声明（${endpoint ? ENDPOINTS[endpoint].label : "未选端点"}）`}</span>
                </span>
              )}
            </div>
            <label className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
              覆盖
              <Switch size="sm" checked={on} onCheckedChange={(c) => setDim(key, c ? structuredClone(inherited[key] ?? (key === "maxReferenceImages" ? 0 : [])) : undefined)} />
            </label>
          </div>
        );
      })}
    </div>
  );
}

function OverrideInput({ dim, value, onChange }: { dim: Dim; value: CapabilityOverride[Dim]; onChange: (v: CapabilityOverride[Dim]) => void }) {
  if (dim === "durations")
    return (
      <Input
        aria-label="覆盖可选时长"
        className="h-7 w-48"
        defaultValue={(value as number[]).join(", ")}
        onChange={(e) => onChange(e.target.value.split(/[,，\s]+/).map(Number).filter((n) => n > 0))}
      />
    );
  if (dim === "maxReferenceImages")
    return <Input aria-label="覆盖参考图上限" inputMode="numeric" className="h-7 w-20 text-right" value={String(value ?? 0)} onChange={(e) => onChange(Number(e.target.value) || 0)} />;
  const all = dim === "resolutions" ? ["480p", "720p", "1080p", "4k"] : (Object.keys(INPUT_MODE_LABEL) as (keyof typeof INPUT_MODE_LABEL)[]);
  const cur = value as string[];
  return (
    <div className="flex flex-wrap gap-1.5">
      {all.map((v) => {
        const on = cur.includes(v);
        return (
          <button
            key={v}
            type="button"
            aria-pressed={on}
            onClick={() => onChange((on ? cur.filter((x) => x !== v) : [...cur, v]))}
            className={cn("h-6 rounded-md border px-2 text-xs", on ? "border-primary/50 bg-primary/10" : "border-border text-muted-foreground")}
          >
            {dim === "inputModes" ? INPUT_MODE_LABEL[v as keyof typeof INPUT_MODE_LABEL] : v}
          </button>
        );
      })}
    </div>
  );
}

// ---------- 路径覆盖 ----------

export function PathOverrideTable({
  baseUrl,
  endpoint,
  apiModelName,
  value,
  onChange,
}: {
  baseUrl: string;
  endpoint: EndpointKey;
  apiModelName: string;
  value: Record<string, string> | undefined;
  onChange: (next: Record<string, string>) => void;
}) {
  const def = ENDPOINTS[endpoint];
  const ov = value ?? {};
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <table className="w-full table-fixed text-sm">
        <thead className="bg-muted/40 text-xs text-muted-foreground">
          <tr>
            <th className="w-16 px-3 py-2 text-left font-normal">请求</th>
            <th className="w-14 px-1 py-2 text-left font-normal">方法</th>
            <th className="w-[14rem] px-2 py-2 text-left font-normal">路径</th>
            <th className="px-3 py-2 text-left font-normal">完整 URL</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {def.requests.map((req) => {
            const p = previewUrl(baseUrl || "https://<base_url>", def, req, ov[req.name], apiModelName || "<model>");
            const base = (baseUrl || "https://<base_url>").replace(/\/+$/, "");
            const rest = p.url.slice(base.length + (p.autoAdded?.length ?? 0));
            return (
              <tr key={req.name} className="align-middle">
                <td className="px-3 py-1.5">
                  <div>{req.label}</div>
                  <div className="font-mono text-xs text-subtle-foreground">{req.name}</div>
                </td>
                <td className="px-1 py-1.5 font-mono text-xs text-muted-foreground">{req.method}</td>
                <td className="px-2 py-1.5">
                  <Input
                    aria-label={`${req.label}请求的路径覆盖`}
                    className="h-7 font-mono text-xs placeholder:text-subtle-foreground"
                    placeholder={`默认 ${req.defaultPath}`}
                    value={ov[req.name] ?? ""}
                    onChange={(e) => {
                      const next = { ...ov };
                      if (e.target.value) next[req.name] = e.target.value;
                      else delete next[req.name];
                      onChange(next);
                    }}
                  />
                </td>
                <td className="px-3 py-1.5">
                  <code className="block font-mono text-xs break-all">
                    <span className="text-muted-foreground">{base}</span>
                    {p.autoAdded && <span className="text-subtle-foreground">{p.autoAdded}</span>}
                    <span className={cn(p.overridden ? "rounded-sm bg-primary/15 text-foreground" : "text-muted-foreground")}>{rest}</span>
                  </code>
                  {p.autoAdded && <span className="text-xs text-subtle-foreground">未覆盖时自动补 /v1</span>}
                  {p.overridden && def.autoV1 && !/\/v\d+[a-z]*$/.test(base) && <span className="text-xs text-subtle-foreground">已覆盖，不再自动补 /v1</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
