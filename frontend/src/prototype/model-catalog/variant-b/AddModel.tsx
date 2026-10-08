// PROTOTYPE — #3091 变体 B 浏览整个模型目录的唯一入口：「添加模型」与「换一个条目」共用的 Command 面板，
// 以及目录里没有时的「新建条目」对话框。

import { useMemo, useState } from "react";
import { Plus, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { CommandDialog, Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  MEDIA_LABEL,
  MEDIA_TYPES,
  PRICE_PRESET_LABEL,
  effectiveOf,
  makerLabel,
  priceFromPreset,
  priceSummary,
  statusOf,
  type CatalogEntry,
  type Currency,
  type EntryData,
  type MediaType,
  type PricePreset,
  type Provider,
} from "../model";
import { useProto } from "../store";
import { Chip, DEFAULT_ENDPOINT, StatusBadge } from "./bits";

function matchEntry(entries: CatalogEntry[], apiName: string): CatalogEntry | undefined {
  const n = apiName.toLowerCase();
  return entries.find((e) => {
    const d = effectiveOf(e);
    return d.name.toLowerCase() === n || e.id.toLowerCase() === n || (d.aliases ?? []).some((a) => a.toLowerCase() === n);
  });
}

function EntryItem({ entry, onSelect, suffix }: { entry: CatalogEntry; onSelect: () => void; suffix?: React.ReactNode }) {
  const d = effectiveOf(entry);
  return (
    <CommandItem value={`${entry.id} ${d.displayName} ${(d.aliases ?? []).join(" ")}`} onSelect={onSelect} className="flex items-center gap-3 py-1.5">
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="flex items-center gap-2 truncate">
          {d.displayName}
          {d.deprecated && <span className="text-xs text-destructive">官方已下架</span>}
        </span>
        <span className="truncate text-xs text-muted-foreground">
          {makerLabel(d.maker)} · {MEDIA_LABEL[d.mediaType]} · {priceSummary(d.price)}
        </span>
      </div>
      {suffix}
      <StatusBadge status={statusOf(entry)} />
    </CommandItem>
  );
}

/** 在整个模型目录里选一个条目。 */
export function EntryPicker({
  open,
  onOpenChange,
  onPick,
  media,
  onCreate,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onPick: (entryId: string) => void;
  media?: MediaType;
  onCreate?: () => void;
}) {
  const entries = useProto((s) => s.entries);
  const [filter, setFilter] = useState<MediaType | "all">(media ?? "all");
  const list = entries.filter((e) => filter === "all" || effectiveOf(e).mediaType === filter);
  return (
    <CommandDialog open={open} onOpenChange={onOpenChange} title="选择模型条目" description="在模型目录里搜索" className="top-[8dvh] sm:max-w-2xl">
      <Command>
        <CommandInput placeholder="搜索模型名、条目 id 或别名" />
        <MediaChips value={filter} onChange={setFilter} entries={entries} />
        <CommandList className="max-h-[calc(84dvh-150px)]">
          <CommandEmpty>目录里没有匹配的条目。</CommandEmpty>
          <CommandGroup heading={`模型目录 · ${list.length} 个条目`}>
            {list.map((e) => (
              <EntryItem
                key={e.id}
                entry={e}
                onSelect={() => {
                  onPick(e.id);
                  onOpenChange(false);
                }}
              />
            ))}
          </CommandGroup>
        </CommandList>
        {onCreate && <CreateFooter onCreate={onCreate} />}
      </Command>
    </CommandDialog>
  );
}

function MediaChips({ value, onChange, entries }: { value: MediaType | "all"; onChange: (v: MediaType | "all") => void; entries: CatalogEntry[] }) {
  return (
    <div className="flex flex-wrap gap-1.5 border-b border-border px-2 py-2">
      <Chip active={value === "all"} onClick={() => onChange("all")} count={entries.length}>
        全部
      </Chip>
      {MEDIA_TYPES.map((m) => (
        <Chip key={m} active={value === m} onClick={() => onChange(m)} count={entries.filter((e) => effectiveOf(e).mediaType === m).length}>
          {MEDIA_LABEL[m]}
        </Chip>
      ))}
    </div>
  );
}

function CreateFooter({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="flex items-center gap-2 border-t border-border px-3 py-2 text-sm">
      <span className="text-muted-foreground">目录里没有？</span>
      <Button variant="ghost" size="sm" onClick={onCreate}>
        <Plus aria-hidden />
        新建条目
      </Button>
    </div>
  );
}

/** 「添加模型」：内置供应商先列官方收录未启用的、获取模型得到的未收录 id，再列整个目录。 */
export function AddModelDialog({
  provider,
  open,
  onOpenChange,
  onAdded,
}: {
  provider: Provider;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onAdded: (pmKey: string) => void;
}) {
  const entries = useProto((s) => s.entries);
  const addProviderModel = useProto((s) => s.addProviderModel);
  const updateProviderModel = useProto((s) => s.updateProviderModel);
  const [filter, setFilter] = useState<MediaType | "all">("all");
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const builtin = provider.kind === "builtin";

  const inFilter = (id: string | null) => {
    const e = entries.find((x) => x.id === id);
    return filter === "all" || (e ? effectiveOf(e).mediaType === filter : false);
  };
  const disabledOfficial = builtin ? provider.models.filter((m) => m.responsibility === "official" && !m.enabled && inFilter(m.entryId)) : [];
  const listedNames = new Set(provider.models.map((m) => m.apiModelName));
  const unlisted = builtin
    ? (provider.discovered ?? []).filter((id) => !listedNames.has(id) && (filter === "all" || inFilter(matchEntry(entries, id)?.id ?? null)))
    : [];
  const catalog = useMemo(() => entries.filter((e) => filter === "all" || effectiveOf(e).mediaType === filter), [entries, filter]);
  const entryOf = (id: string | null) => entries.find((e) => e.id === id);

  const add = (apiModelName: string, entryId: string | null, media?: MediaType) => {
    const key = `${provider.id}:${apiModelName}:${Date.now()}`;
    addProviderModel(provider.id, {
      key,
      entryId,
      apiModelName,
      enabled: true,
      isDefault: false,
      responsibility: "user",
      multiplier: 1,
      ...(provider.kind === "custom" ? { endpoint: DEFAULT_ENDPOINT[media ?? "text"] } : {}),
    });
    onOpenChange(false);
    onAdded(key);
  };

  return (
    <>
      <CommandDialog open={open && !creating} onOpenChange={onOpenChange} title={`给 ${provider.displayName} 添加模型`} description="在模型目录里搜索" className="top-[8dvh] sm:max-w-2xl">
        <Command>
          <CommandInput placeholder="搜索模型名、条目 id 或 API 模型名" value={query} onValueChange={setQuery} />
          <MediaChips value={filter} onChange={setFilter} entries={entries} />
          <CommandList className="max-h-[calc(84dvh-150px)]">
            <CommandEmpty>没有匹配的模型。</CommandEmpty>
            {disabledOfficial.length > 0 && (
              <CommandGroup heading="官方收录，尚未启用">
                {disabledOfficial.map((m) => {
                  const e = entryOf(m.entryId);
                  return e ? (
                    <EntryItem
                      key={m.key}
                      entry={e}
                      suffix={<span className="text-xs text-muted-foreground">启用</span>}
                      onSelect={() => {
                        updateProviderModel(provider.id, m.key, { enabled: true });
                        onOpenChange(false);
                        onAdded(m.key);
                      }}
                    />
                  ) : null;
                })}
              </CommandGroup>
            )}
            {builtin && provider.discoverable && unlisted.length > 0 && (
              <CommandGroup heading="获取模型得到、官方未收录">
                {unlisted.map((id) => {
                  const e = matchEntry(entries, id);
                  return (
                    <CommandItem key={id} value={`discovered ${id}`} onSelect={() => add(id, e?.id ?? null, e ? effectiveOf(e).mediaType : undefined)}>
                      <div className="flex min-w-0 flex-1 flex-col">
                        <span className="font-mono text-sm">{id}</span>
                        <span className="text-xs text-muted-foreground">
                          {e ? `对应条目 ${effectiveOf(e).displayName}` : "没有对应的条目，添加后记为未定价，可再选择或新建条目"}
                        </span>
                      </div>
                      <span className="text-xs text-muted-foreground">由你负责</span>
                    </CommandItem>
                  );
                })}
              </CommandGroup>
            )}
            {builtin && !provider.discoverable && (
              <div className="px-3 py-2 text-xs text-muted-foreground">{provider.displayName} 没有模型列表接口，官方未收录的模型只能按 id 手动添加。</div>
            )}
            {query.trim() && (
              <CommandGroup heading="按 id 添加">
                <CommandItem value={`manual ${query}`} onSelect={() => add(query.trim(), matchEntry(entries, query.trim())?.id ?? null)}>
                  <Search aria-hidden />
                  添加 API 模型名「<span className="font-mono">{query.trim()}</span>」
                </CommandItem>
              </CommandGroup>
            )}
            <CommandGroup heading={`模型目录 · ${catalog.length} 个条目`}>
              {catalog.map((e) => {
                const d = effectiveOf(e);
                return <EntryItem key={e.id} entry={e} onSelect={() => add(d.name, e.id, d.mediaType)} />;
              })}
            </CommandGroup>
          </CommandList>
          <CreateFooter onCreate={() => setCreating(true)} />
        </Command>
      </CommandDialog>
      <NewEntryDialog
        open={creating}
        initialName={query.trim()}
        onOpenChange={(v) => {
          setCreating(v);
        }}
        onCreated={(data) => {
          setCreating(false);
          add(data.name, data.id, data.mediaType);
        }}
      />
    </>
  );
}

/** 目录里没有的模型由用户新建条目，新建即关闭同步（ADR 0098）。价格先按预设生成通用结构。 */
export function NewEntryDialog({
  open,
  onOpenChange,
  onCreated,
  initialName = "",
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onCreated: (data: EntryData) => void;
  initialName?: string;
}) {
  const createEntry = useProto((s) => s.createEntry);
  const entries = useProto((s) => s.entries);
  const [maker, setMaker] = useState("");
  const [name, setName] = useState(initialName);
  const [displayName, setDisplayName] = useState("");
  const [media, setMedia] = useState<MediaType>("text");
  const [preset, setPreset] = useState<PricePreset>("token");
  const [currency, setCurrency] = useState<Currency>("USD");
  const id = `${maker.trim() || "custom"}/${name.trim()}`;
  const taken = entries.some((e) => e.id === id);
  const valid = name.trim() && !taken;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>新建模型条目</DialogTitle>
          <DialogDescription>目录里没有的模型由你新建并负责；日后官方收录了同一身份，可以打开官方同步改用官方数据。</DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-3">
          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1 text-sm">
              模型厂商
              <Input value={maker} onChange={(e) => setMaker(e.target.value)} placeholder="如 bfl" />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              官方模型名
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="如 flux-kontext-max" />
            </label>
          </div>
          <p className="text-xs text-muted-foreground">
            条目 id：<span className="font-mono">{id}</span>
            {taken && <span className="ml-2 text-warn">已存在，请直接选用</span>}
          </p>
          <label className="flex flex-col gap-1 text-sm">
            显示名
            <Input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder={name || "显示在模型选择器里的名字"} />
          </label>
          <div className="flex flex-col gap-1 text-sm">
            媒体类型
            <div className="flex gap-1.5">
              {MEDIA_TYPES.map((m) => (
                <Chip key={m} active={media === m} onClick={() => setMedia(m)}>
                  {MEDIA_LABEL[m]}
                </Chip>
              ))}
            </div>
          </div>
          <div className="flex flex-col gap-1 text-sm">
            计价方式
            <div className="flex flex-wrap gap-1.5">
              {(Object.keys(PRICE_PRESET_LABEL) as PricePreset[]).map((p) => (
                <Chip key={p} active={preset === p} onClick={() => setPreset(p)}>
                  {PRICE_PRESET_LABEL[p]}
                </Chip>
              ))}
              <span className="mx-1 h-7 w-px bg-border" />
              {(["USD", "CNY"] as Currency[]).map((c) => (
                <Chip key={c} active={currency === c} onClick={() => setCurrency(c)}>
                  {c}
                </Chip>
              ))}
            </div>
            <span className="text-xs text-muted-foreground">预设只决定初始的计量项，建好后可以继续加档、加条件。</span>
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            disabled={!valid}
            onClick={() => {
              const data: EntryData = {
                id,
                maker: maker.trim() || "custom",
                name: name.trim(),
                displayName: displayName.trim() || name.trim(),
                mediaType: media,
                capabilities: media === "text" ? ["text_generation"] : media === "image" ? ["text_to_image"] : media === "audio" ? ["text_to_speech"] : [],
                durations: media === "video" ? [5, 10] : [],
                resolutions: [],
                durationConstraints: {},
                maxOutputTokens: media === "text" ? 8192 : null,
                price: priceFromPreset(preset, currency),
                version: "user",
              };
              createEntry(data);
              onCreated(data);
            }}
          >
            新建条目
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
