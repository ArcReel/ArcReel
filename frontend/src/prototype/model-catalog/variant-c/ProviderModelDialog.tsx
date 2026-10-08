// PROTOTYPE — #3091 变体 C：编辑一个用户负责的供应商模型（一条渠道）。
// 渠道事实都在这里：API 模型名、调用端点、供应商模型倍率、能力覆盖、路径覆盖。Dialog 自己保存，立即生效。
import { useState } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "cn";
import {
  ENDPOINTS,
  INPUT_MODE_LABEL,
  channelVideoCaps,
  effectiveMultiplier,
  previewUrl,
  type CapabilityOverride,
  type EndpointDef,
  type EndpointKey,
  type EntryData,
  type Provider,
  type ProviderModel,
} from "../model";
import { useProto } from "../store";
import { SimpleSelect } from "./shared";

export function ProviderModelDialog({
  open,
  onOpenChange,
  provider,
  model,
  entry,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  provider: Provider;
  model: ProviderModel;
  entry: EntryData | null;
}) {
  const update = useProto((s) => s.updateProviderModel);
  const [draft, setDraft] = useState<ProviderModel>(model);
  const [lastOpen, setLastOpen] = useState(open);
  if (open !== lastOpen) {
    setLastOpen(open);
    if (open) setDraft(structuredClone(model));
  }

  const isCustom = provider.kind === "custom";
  const endpoint = draft.endpoint ? ENDPOINTS[draft.endpoint] : null;
  const endpointChanged = draft.endpoint !== model.endpoint;
  const hadOverrides = Object.values(model.pathOverrides ?? {}).some(Boolean);
  const mediaType = entry?.mediaType ?? endpoint?.mediaType;
  const endpointItems = (Object.values(ENDPOINTS))
    .filter((e) => !mediaType || e.mediaType === mediaType)
    .map((e) => ({ value: e.key, label: e.label, hint: e.kind === "declarative" ? "声明式" : undefined }));

  const save = () => {
    // 共用 store 在改挂端点时会清空路径覆盖：先改端点，再写新端点下填的覆盖
    const { pathOverrides, ...rest } = draft;
    update(provider.id, model.key, rest);
    update(provider.id, model.key, { pathOverrides: pathOverrides ?? {} });
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="xl">
        <DialogHeader>
          <DialogTitle>
            {provider.displayName} · {model.apiModelName}
          </DialogTitle>
          <DialogDescription>这条渠道由你负责：这里的改动只影响 {provider.displayName} 下的这个供应商模型，不改模型条目。</DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-7">
          <section className="grid grid-cols-[9rem_1fr] items-center gap-x-4 gap-y-3">
            <label htmlFor="pm-api" className="text-sm text-subtle-foreground">
              API 模型名
            </label>
            <Input id="pm-api" className="font-mono" value={draft.apiModelName} onChange={(e) => setDraft((d) => ({ ...d, apiModelName: e.target.value }))} />
            {isCustom && (
              <>
                <label className="text-sm text-subtle-foreground">调用端点</label>
                <div className="flex flex-col gap-1.5">
                  <SimpleSelect<EndpointKey> value={draft.endpoint ?? null} onChange={(k) => setDraft((d) => ({ ...d, endpoint: k, pathOverrides: {} }))} items={endpointItems} />
                  {endpointChanged && hadOverrides && (
                    <p className="flex items-center gap-1.5 text-xs text-warn">
                      <AlertTriangle aria-hidden className="size-3.5" />
                      改挂端点后，原有路径覆盖将丢弃。
                    </p>
                  )}
                </div>
              </>
            )}
            <label htmlFor="pm-mul" className="text-sm text-subtle-foreground">
              供应商模型倍率
            </label>
            <div className="flex items-center gap-3">
              <Input id="pm-mul" type="number" step="0.05" className="w-28 tabular-nums" value={draft.multiplier} onChange={(e) => setDraft((d) => ({ ...d, multiplier: Number(e.target.value) }))} />
              <span className="text-sm text-muted-foreground">
                × 供应商倍率 {provider.multiplier} = 生效倍率 <span className="text-foreground tabular-nums">{effectiveMultiplier(provider, draft)}</span>
              </span>
            </div>
          </section>

          {entry && (mediaType === "video" || mediaType === "image") && <CapabilitySection entry={entry} endpoint={draft.endpoint} value={draft.capabilityOverride ?? {}} onChange={(o) => setDraft((d) => ({ ...d, capabilityOverride: o }))} />}

          {isCustom && endpoint && <PathSection provider={provider} endpoint={endpoint} model={draft} onChange={(p) => setDraft((d) => ({ ...d, pathOverrides: p }))} />}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={save}>保存</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------- 能力覆盖 ----------

type CapDim = keyof CapabilityOverride;
const CAP_LABEL: Record<CapDim, string> = { durations: "可选时长", resolutions: "分辨率", inputModes: "输入模式", maxReferenceImages: "参考图上限" };

function fmtCap(dim: CapDim, v: CapabilityOverride[CapDim] | undefined): string {
  if (v === undefined) return "—";
  if (dim === "durations") return (v as number[]).length ? `${(v as number[]).join("、")} 秒` : "无";
  if (dim === "inputModes") return (v as (keyof typeof INPUT_MODE_LABEL)[]).map((m) => INPUT_MODE_LABEL[m]).join("、") || "无";
  if (dim === "maxReferenceImages") return `${v} 张`;
  return (v as string[]).join("、") || "无";
}

function parseCap(dim: CapDim, text: string): CapabilityOverride[CapDim] {
  const parts = text.split(/[,，、\s]+/).filter(Boolean);
  if (dim === "durations") return parts.map(Number).filter((n) => n > 0);
  if (dim === "maxReferenceImages") return Number(text) || 0;
  if (dim === "inputModes") {
    const rev = Object.fromEntries(Object.entries(INPUT_MODE_LABEL).map(([k, v]) => [v, k]));
    return parts.map((p) => rev[p] ?? p).filter((p) => p in INPUT_MODE_LABEL);
  }
  return parts;
}

function rawText(dim: CapDim, v: CapabilityOverride[CapDim] | undefined): string {
  if (v === undefined) return "";
  if (dim === "inputModes") return (v as (keyof typeof INPUT_MODE_LABEL)[]).map((m) => INPUT_MODE_LABEL[m]).join("、");
  if (Array.isArray(v)) return v.join("、");
  return String(v);
}

function CapabilitySection({ entry, endpoint, value, onChange }: { entry: EntryData; endpoint?: EndpointKey; value: CapabilityOverride; onChange: (o: CapabilityOverride) => void }) {
  const channel = channelVideoCaps(endpoint);
  const dims: { dim: CapDim; source: "条目" | "端点声明"; base: CapabilityOverride[CapDim] }[] =
    entry.mediaType === "video"
      ? [
          { dim: "durations", source: "条目", base: entry.durations },
          { dim: "resolutions", source: "条目", base: entry.resolutions },
          { dim: "inputModes", source: "端点声明", base: channel.inputModes },
          { dim: "maxReferenceImages", source: "端点声明", base: channel.maxReferenceImages },
        ]
      : [{ dim: "resolutions", source: "条目", base: entry.resolutions }];
  const [editing, setEditing] = useState<CapDim | null>(null);
  const [text, setText] = useState("");

  return (
    <section className="flex flex-col gap-2">
      <div className="flex flex-col gap-1">
        <h3 className="text-sm font-medium">能力覆盖</h3>
        <p className="text-sm text-muted-foreground">渠道的能力与条目或端点声明不一致时，在这里按维度整体替换，优先级最高。</p>
      </div>
      <div className="divide-y divide-border rounded-lg border border-border">
        {dims.map(({ dim, source, base }) => {
          const overridden = value[dim] !== undefined;
          return (
            <div key={dim} className="flex items-center gap-3 px-3 py-2">
              <span className="w-24 shrink-0 text-sm text-subtle-foreground">{CAP_LABEL[dim]}</span>
              {editing === dim ? (
                <Input autoFocus className="h-7 min-w-0 flex-1" value={text} onChange={(e) => setText(e.target.value)} placeholder={rawText(dim, base)} />
              ) : (
                <span className={cn("min-w-0 flex-1 truncate text-sm", overridden && "text-foreground")}>
                  {fmtCap(dim, overridden ? value[dim] : base)}
                  {overridden && <span className="ml-2 text-xs text-muted-foreground line-through">{fmtCap(dim, base)}</span>}
                </span>
              )}
              <span className={cn("shrink-0 rounded-md px-1.5 py-0.5 text-xs", overridden ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground")}>来自{overridden ? "覆盖" : source}</span>
              {editing === dim ? (
                <span className="flex shrink-0 gap-1">
                  <Button size="xs" variant="outline" onClick={() => setEditing(null)}>
                    取消
                  </Button>
                  <Button
                    size="xs"
                    onClick={() => {
                      onChange({ ...value, [dim]: parseCap(dim, text) });
                      setEditing(null);
                    }}
                  >
                    确定
                  </Button>
                </span>
              ) : (
                <span className="flex shrink-0 gap-1">
                  {overridden && (
                    <Button
                      size="icon-xs"
                      variant="ghost"
                      aria-label="移除覆盖"
                      title="移除覆盖"
                      onClick={() => {
                        const next = { ...value };
                        delete next[dim];
                        onChange(next);
                      }}
                    >
                      <RotateCcw aria-hidden />
                    </Button>
                  )}
                  <Button
                    size="xs"
                    variant="ghost"
                    onClick={() => {
                      setText(rawText(dim, overridden ? value[dim] : base));
                      setEditing(dim);
                    }}
                  >
                    覆盖
                  </Button>
                </span>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

// ---------- 路径覆盖 ----------

function PathSection({ provider, endpoint, model, onChange }: { provider: Provider; endpoint: EndpointDef; model: ProviderModel; onChange: (p: Record<string, string>) => void }) {
  const overrides = model.pathOverrides ?? {};
  const base = provider.baseUrl ?? "";
  const urls = endpoint.requests.map((r) => ({ r, p: previewUrl(base, endpoint, r, overrides[r.name], model.apiModelName) }));

  return (
    <section className="flex flex-col gap-2">
      <div className="flex flex-col gap-1">
        <h3 className="text-sm font-medium">路径覆盖</h3>
        <p className="text-sm text-muted-foreground">
          中转站改了路径时，按请求整条替换。路径相对于 base_url 原样值（<span className="font-mono text-xs">{base}</span>），覆盖后不再自动补 /v1。模型发现与连通性检查不受影响。
        </p>
      </div>
      <div className="divide-y divide-border rounded-lg border border-border">
        {urls.map(({ r, p }) => {
          const on = r.name in overrides;
          return (
            <div key={r.name} className="flex flex-col gap-1.5 px-3 py-2.5">
              <div className="flex items-center gap-3">
                <span className="w-20 shrink-0 text-sm">
                  {r.label}
                  <span className="ml-1 font-mono text-xs text-muted-foreground">{r.name}</span>
                </span>
                <ToggleGroup
                  aria-label={`${r.label}的路径`}
                  variant="outline"
                  size="sm"
                  spacing={0}
                  value={[on ? "override" : "default"]}
                  onValueChange={(next: string[]) => {
                    if (!next[0]) return;
                    const copy = { ...overrides };
                    if (next[0] === "override") copy[r.name] = overrides[r.name] ?? r.defaultPath;
                    else delete copy[r.name];
                    onChange(copy);
                  }}
                >
                  <ToggleGroupItem value="default">默认</ToggleGroupItem>
                  <ToggleGroupItem value="override" className="aria-pressed:bg-primary/15 aria-pressed:text-primary">
                    覆盖
                  </ToggleGroupItem>
                </ToggleGroup>
                <span className="w-10 shrink-0 font-mono text-xs text-muted-foreground">{r.method}</span>
                {on ? (
                  <Input className="h-7 min-w-0 flex-1 font-mono text-xs" aria-label={`${r.label}的覆盖路径`} value={overrides[r.name]} onChange={(e) => onChange({ ...overrides, [r.name]: e.target.value })} />
                ) : (
                  <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground">{r.defaultPath}</span>
                )}
              </div>
              <UrlLine base={base} preview={p} />
            </div>
          );
        })}
      </div>
      <div className="flex flex-col gap-1.5 pt-2">
        <span className="text-xs text-muted-foreground">这个模型会请求的全部 URL</span>
        <pre className="overflow-x-auto rounded-lg bg-muted/50 px-3 py-2 font-mono text-xs leading-6">
          {urls.map(({ r, p }) => `${r.method.padEnd(4)} ${p.url}`).join("\n")}
        </pre>
      </div>
    </section>
  );
}

function UrlLine({ base, preview }: { base: string; preview: ReturnType<typeof previewUrl> }) {
  const b = base.replace(/\/+$/, "");
  const rest = preview.url.slice(b.length);
  const auto = preview.autoAdded;
  return (
    <p className="truncate pl-23 font-mono text-xs" title={preview.url}>
      <span className="text-muted-foreground">{b}</span>
      {auto && <span className="text-warn" title="未覆盖时自动补上的 /v1">{auto}</span>}
      <span className={preview.overridden ? "text-primary" : "text-subtle-foreground"}>{auto ? rest.slice(auto.length) : rest}</span>
    </p>
  );
}
