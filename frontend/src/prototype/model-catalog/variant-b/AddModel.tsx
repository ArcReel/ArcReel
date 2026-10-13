// PROTOTYPE — #3091 变体 B 浏览整个模型目录的唯一入口：「添加模型」与「更换条目」共用的 Command 面板，
// 以及目录中没有时的「新建条目」对话框。批量拉取供应商的模型列表另见 DiscoverModels。

import { useMemo, useState } from "react";
import { Plus, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { CommandDialog, Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  MEDIA_LABEL,
  MEDIA_TYPES,
  effectiveOf,
  makerLabel,
  priceSummary,
  type CatalogEntry,
  type EntryData,
  type MediaType,
  type PriceTable,
  type Provider,
} from "../model";
import { useProto } from "../store";
import { Chip, endpointFor, matchEntry } from "./bits";
import { PriceCards } from "./PriceCards";

function EntryItem({ entry, onSelect, suffix }: { entry: CatalogEntry; onSelect: () => void; suffix?: React.ReactNode }) {
  const d = effectiveOf(entry);
  return (
    <CommandItem value={`${entry.id} ${d.displayName} ${(d.aliases ?? []).join(" ")}`} onSelect={onSelect} className="flex items-center gap-3 py-1.5">
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="flex items-center gap-2 truncate">
          {d.displayName}
          {d.deprecated && <span className="text-xs text-destructive">已下架</span>}
        </span>
        <span className="flex gap-3 truncate text-xs text-muted-foreground">
          <span>{makerLabel(d.maker)}</span>
          <span>{MEDIA_LABEL[d.mediaType]}</span>
          <span>{priceSummary(d.price)}</span>
        </span>
      </div>
      {suffix}
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
    <CommandDialog open={open} onOpenChange={onOpenChange} title="从模型目录选择" description="搜索模型目录" className="top-[8dvh] sm:max-w-2xl">
      <Command>
        <CommandInput placeholder="搜索模型名称" />
        <MediaChips value={filter} onChange={setFilter} entries={entries} />
        <CommandList className="max-h-[calc(84dvh-150px)]">
          <CommandEmpty>模型目录中没有匹配的模型。</CommandEmpty>
          <CommandGroup heading={`模型目录（${list.length}）`}>
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
      <span className="text-muted-foreground">目录中找不到？</span>
      <Button variant="ghost" size="sm" className="ml-auto" onClick={onCreate}>
        <Plus aria-hidden />
        手动填写
      </Button>
    </div>
  );
}

/** 「添加模型」：内置供应商先列官方收录、未启用的模型，再列整个目录；也可以直接按 API 模型名添加。 */
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
      ...(provider.kind === "custom" ? { endpoint: endpointFor(provider, media ?? "text", entries) } : {}),
    });
    onOpenChange(false);
    onAdded(key);
  };

  return (
    <>
      <CommandDialog open={open && !creating} onOpenChange={onOpenChange} title={`添加模型到「${provider.displayName}」`} description="搜索模型目录" className="top-[8dvh] sm:max-w-2xl">
        <Command>
          <CommandInput placeholder="搜索模型名称，或输入模型 ID" value={query} onValueChange={setQuery} />
          <MediaChips value={filter} onChange={setFilter} entries={entries} />
          <CommandList className="max-h-[calc(84dvh-150px)]">
            <CommandEmpty>没有匹配的模型。</CommandEmpty>
            {disabledOfficial.length > 0 && (
              <CommandGroup heading="未启用的模型">
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
            {query.trim() && (
              <CommandGroup heading="按模型 ID 添加">
                <CommandItem value={`manual ${query}`} onSelect={() => add(query.trim(), matchEntry(entries, query.trim())?.id ?? null)}>
                  <Search aria-hidden />
                  添加「<span className="font-mono">{query.trim()}</span>」
                </CommandItem>
              </CommandGroup>
            )}
            <CommandGroup heading={`模型目录（${catalog.length}）`}>
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
  const [price, setPrice] = useState<PriceTable>({ currency: "USD", meters: [] });
  const id = `${maker.trim() || "custom"}/${name.trim()}`;
  const taken = entries.some((e) => e.id === id);
  const valid = name.trim() && !taken;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>手动填写模型信息</DialogTitle>
          <DialogDescription>为目录中没有的模型填写基本信息，价格可以之后再补。</DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-3">
          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1 text-sm">
              模型厂商
              <Input value={maker} onChange={(e) => setMaker(e.target.value)} placeholder="例如 bfl" />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              模型名称
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="例如 flux-kontext-max" />
            </label>
          </div>
          {taken && <p className="text-xs text-warn">模型目录中已有这个模型，请直接选择。</p>}
          <label className="flex flex-col gap-1 text-sm">
            显示名称
            <Input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder={name || "选择模型时看到的名称"} />
          </label>
          <div className="flex flex-col gap-1 text-sm">
            类型
            <div className="flex gap-1.5">
              {MEDIA_TYPES.map((m) => (
                <Chip key={m} active={media === m} onClick={() => setMedia(m)}>
                  {MEDIA_LABEL[m]}
                </Chip>
              ))}
            </div>
          </div>
          <div className="flex flex-col gap-1.5 text-sm">
            价格（可选）
            <PriceCards value={price} onChange={setPrice} media={media} optional />
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
                price,
                version: "user",
              };
              createEntry(data);
              onCreated(data);
            }}
          >
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
