// PROTOTYPE — #3091 变体 A：「模型目录」分区。顶部同步状态条；左侧条目表格与筛选；右侧条目详情。
import { useEffect, useMemo, useRef, useState } from "react";
import { CloudOff, Loader2, Plus, RefreshCw, Search } from "lucide-react";
import { cn } from "cn";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Popover, PopoverContent, PopoverTitle, PopoverTrigger } from "@/components/ui/popover";
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
} from "../model";
import { referencesOf, useProto, type SyncChange } from "../store";
import { EntryDetail } from "./EntryDetail";
import { Segmented, SimpleSelect, StatusBadge, UnpricedBadge, isUnpriced, useGo, useParam } from "./shared";

type StatusFilter = "all" | "outdated" | "modified" | "user" | "unpriced" | "deprecated";
const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "全部状态" },
  { value: "outdated", label: "有官方更新" },
  { value: "modified", label: "已修改" },
  { value: "user", label: "用户新建" },
  { value: "unpriced", label: "未定价" },
  { value: "deprecated", label: "已下架" },
];

function matchesStatus(e: CatalogEntry, f: StatusFilter): boolean {
  const s = statusOf(e);
  switch (f) {
    case "all":
      return true;
    case "outdated":
      return s === "modified-outdated" || s === "user-created-official";
    case "modified":
      return s === "modified" || s === "modified-outdated";
    case "user":
      return s === "user-created" || s === "user-created-official";
    case "unpriced":
      return isUnpriced(e);
    case "deprecated":
      return !!effectiveOf(e).deprecated;
  }
}

export function ModelsView() {
  const entries = useProto((s) => s.entries);
  const providers = useProto((s) => s.providers);
  const go = useGo();
  const selectedId = useParam("entry");

  const [query, setQuery] = useState("");
  const [media, setMedia] = useState<"all" | MediaType>("all");
  const [maker, setMaker] = useState("all");
  const [provider, setProvider] = useState("all");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [creating, setCreating] = useState(false);

  const makers = useMemo(() => [...new Set(entries.map((e) => effectiveOf(e).maker))].sort((a, b) => makerLabel(a).localeCompare(makerLabel(b))), [entries]);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return entries
      .filter((e) => {
        const d = effectiveOf(e);
        if (media !== "all" && d.mediaType !== media) return false;
        if (maker !== "all" && d.maker !== maker) return false;
        if (provider !== "all" && !providers.find((p) => p.id === provider)?.models.some((m) => m.entryId === e.id)) return false;
        if (!matchesStatus(e, status)) return false;
        if (q && ![d.displayName, e.id, ...(d.aliases ?? [])].some((s) => s.toLowerCase().includes(q))) return false;
        return true;
      })
      .sort((a, b) => makerLabel(effectiveOf(a).maker).localeCompare(makerLabel(effectiveOf(b).maker)) || a.id.localeCompare(b.id));
  }, [entries, providers, query, media, maker, provider, status]);

  const selected = entries.find((e) => e.id === selectedId) ?? rows[0];
  const tableRef = useRef<HTMLTableSectionElement>(null);
  // 从变化列表、被引用链接跳过来时，把选中行滚进视野
  useEffect(() => {
    tableRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }, [selected?.id]);
  const filtered = query || media !== "all" || maker !== "all" || provider !== "all" || status !== "all";

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <SyncBar onPick={(id) => go("models", { entry: id })} />
      <div className="flex min-h-0 min-w-0 flex-1">
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-6 py-3">
            <InputGroup className="w-full @5xl/page:w-56">
              <InputGroupAddon>
                <Search />
              </InputGroupAddon>
              <InputGroupInput aria-label="搜索模型条目" placeholder="名称、条目 id 或别名" value={query} onChange={(e) => setQuery(e.target.value)} />
            </InputGroup>
            <Segmented
              label="媒体类型"
              value={media}
              onChange={setMedia}
              options={[{ value: "all", label: "全部" }, ...MEDIA_TYPES.map((m) => ({ value: m, label: MEDIA_LABEL[m] }))]}
            />
            <SimpleSelect label="模型厂商" value={maker} onChange={setMaker} options={[{ value: "all", label: "全部厂商" }, ...makers.map((m) => ({ value: m, label: makerLabel(m) }))]} className="w-32" />
            <SimpleSelect
              label="被哪个供应商使用"
              value={provider}
              onChange={setProvider}
              options={[{ value: "all", label: "全部供应商" }, ...providers.map((p) => ({ value: p.id, label: p.displayName }))]}
              className="w-36"
            />
            <SimpleSelect label="状态" value={status} onChange={(v) => setStatus(v as StatusFilter)} options={STATUS_FILTERS} className="w-32" />
            <span className="flex-1" />
            <Button size="sm" onClick={() => setCreating(true)}>
              <Plus data-icon="inline-start" />
              新建条目
            </Button>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto [scrollbar-gutter:stable]">
            <table className="w-full table-fixed text-sm">
              <thead className="sticky top-0 z-10 bg-background text-xs text-muted-foreground">
                <tr className="border-b border-border">
                  <th className="px-6 py-2 text-left font-normal">模型</th>
                  <th className="hidden w-28 px-2 py-2 text-left font-normal @7xl/page:table-cell">厂商</th>
                  <th className="hidden w-14 px-2 py-2 text-left font-normal @5xl/page:table-cell">类型</th>
                  <th className="hidden w-52 px-2 py-2 text-left font-normal @6xl/page:table-cell">价格</th>
                  <th className="w-32 px-2 py-2 text-left font-normal @6xl/page:w-40">状态</th>
                  <th className="hidden w-16 px-2 py-2 pr-6 text-right font-normal @7xl/page:table-cell">被引用</th>
                </tr>
              </thead>
              <tbody ref={tableRef}>
                {rows.map((e) => {
                  const d = effectiveOf(e);
                  const refs = referencesOf(providers, e.id).length;
                  const active = e.id === selected?.id;
                  return (
                    <tr
                      key={e.id}
                      aria-selected={active}
                      onClick={() => go("models", { entry: e.id })}
                      className={cn("cursor-pointer border-b border-border/60 hover:bg-muted/40", active && "bg-muted/60 hover:bg-muted/60")}
                    >
                      <td className="px-6 py-2">
                        <div className={cn("truncate", d.deprecated && "text-muted-foreground line-through decoration-subtle-foreground")}>{d.displayName}</div>
                        <div className="truncate font-mono text-xs text-subtle-foreground">{e.id}</div>
                      </td>
                      <td className="hidden truncate px-2 py-2 text-muted-foreground @7xl/page:table-cell">{makerLabel(d.maker)}</td>
                      <td className="hidden px-2 py-2 text-muted-foreground @5xl/page:table-cell">{MEDIA_LABEL[d.mediaType]}</td>
                      <td className="hidden truncate px-2 py-2 text-xs tabular-nums text-muted-foreground @6xl/page:table-cell">
                        {d.price.meters.length ? priceSummary(d.price) : "—"}
                      </td>
                      <td className="px-2 py-2">
                        <div className="flex flex-wrap gap-1">
                          {statusOf(e) === "factory" || statusOf(e) === "synced" ? (
                            !isUnpriced(e) && (
                              <span className="text-xs text-subtle-foreground">{statusOf(e) === "synced" ? "跟随官方 · 已同步更新" : "跟随官方"}</span>
                            )
                          ) : (
                            <StatusBadge entry={e} />
                          )}
                          {isUnpriced(e) && <UnpricedBadge />}
                          {d.deprecated && <Badge variant="outline">已下架</Badge>}
                        </div>
                      </td>
                      <td className="hidden px-2 py-2 pr-6 text-right tabular-nums text-muted-foreground @7xl/page:table-cell">{refs || "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {rows.length === 0 && (
              <div className="flex flex-col items-start gap-2 px-6 py-8 text-sm text-muted-foreground">
                没有符合筛选条件的模型条目。
                {filtered && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setQuery("");
                      setMedia("all");
                      setMaker("all");
                      setProvider("all");
                      setStatus("all");
                    }}
                  >
                    清除筛选
                  </Button>
                )}
              </div>
            )}
          </div>
          <div className="shrink-0 border-t border-border px-6 py-2 text-xs text-subtle-foreground tabular-nums">
            {filtered ? `显示 ${rows.length} / ${entries.length} 个条目` : `共 ${entries.length} 个条目`}
          </div>
        </div>

        <div className="flex w-[26rem] shrink-0 border-l border-border @6xl/page:w-[30rem] @7xl/page:w-[38rem]" data-zone="detail">
          {selected ? (
            <EntryDetail key={selected.id} entry={selected} />
          ) : (
            <p className="p-6 text-sm text-muted-foreground">选择一个模型条目查看详情。</p>
          )}
        </div>
      </div>
      <CreateEntryDialog open={creating} onOpenChange={setCreating} onCreated={(id) => go("models", { entry: id })} />
    </div>
  );
}

const CHANGE_LABEL: Record<SyncChange["kind"], string> = { added: "新收录", updated: "已更新", deprecated: "已下架", skipped: "未应用（你改过）" };

function SyncBar({ onPick }: { onPick: (id: string) => void }) {
  const sync = useProto((s) => s.sync);
  const entries = useProto((s) => s.entries);
  const check = useProto((s) => s.checkForUpdates);
  const [open, setOpen] = useState(false);
  const offline = sync.status === "offline";
  const applied = sync.lastChanges.filter((c) => c.kind !== "skipped");
  const skipped = sync.lastChanges.filter((c) => c.kind === "skipped");
  const nameOf = (id: string) => {
    const e = entries.find((x) => x.id === id);
    return e ? effectiveOf(e).displayName : id;
  };

  return (
    <div className="flex shrink-0 items-center gap-3 border-b border-border px-6 py-2.5 text-sm">
      {offline ? (
        <p className="flex min-w-0 flex-1 items-center gap-2 text-warn">
          <CloudOff className="size-4 shrink-0" />
          <span className="truncate">无法连接目录数据仓库。目录使用出厂快照 {sync.factoryVersion}、上次同步到的 {sync.appliedVersion} 和你的修改。</span>
        </p>
      ) : (
        <p className="min-w-0 flex-1 truncate text-muted-foreground">
          官方数据 <span className="text-foreground tabular-nums">{sync.appliedVersion}</span>，{sync.lastCheckedAt}检查过。有新版本时自动应用，跳过你改过的条目。
        </p>
      )}
      {sync.lastChanges.length > 0 && (
        <Popover open={open} onOpenChange={setOpen}>
          <PopoverTrigger render={<Button variant="ghost" size="sm" />}>
            本次更新 <span className="tabular-nums">{applied.length}</span> 项
            {skipped.length > 0 && <span className="text-warn tabular-nums">，{skipped.length} 项未应用</span>}
          </PopoverTrigger>
          <PopoverContent align="end" className="w-[26rem]">
            <PopoverTitle className="px-1">官方数据 {sync.appliedVersion} 带来的变化</PopoverTitle>
            <ul className="-mx-1 flex max-h-80 flex-col overflow-y-auto">
              {[...applied, ...skipped].map((c) => (
                <li key={c.entryId + c.kind}>
                  <button
                    type="button"
                    className="flex w-full items-start gap-3 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted"
                    onClick={() => {
                      onPick(c.entryId);
                      setOpen(false);
                    }}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate">{nameOf(c.entryId)}</span>
                      <span className="block text-xs text-muted-foreground">{c.summary}</span>
                    </span>
                    <Badge variant="outline" className={cn(c.kind === "skipped" && "border-warn/50 text-warn")}>
                      {CHANGE_LABEL[c.kind]}
                    </Badge>
                  </button>
                </li>
              ))}
            </ul>
          </PopoverContent>
        </Popover>
      )}
      <Button variant="outline" size="sm" disabled={offline || sync.status === "checking"} onClick={check}>
        {sync.status === "checking" ? <Loader2 data-icon="inline-start" className="animate-spin" /> : <RefreshCw data-icon="inline-start" />}
        {sync.status === "checking" ? "正在检查" : "检查更新"}
      </Button>
    </div>
  );
}

function CreateEntryDialog({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (v: boolean) => void; onCreated: (id: string) => void }) {
  const createEntry = useProto((s) => s.createEntry);
  const entries = useProto((s) => s.entries);
  const [maker, setMaker] = useState("");
  const [name, setName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [media, setMedia] = useState<MediaType>("text");
  const [preset, setPreset] = useState<PricePreset>("token");
  const [currency, setCurrency] = useState<Currency>("USD");
  const id = `${maker.trim().toLowerCase()}/${name.trim()}`;
  const taken = entries.some((e) => e.id === id);
  const valid = maker.trim() && name.trim() && !taken;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>新建模型条目</DialogTitle>
          <DialogDescription>目录里没有的模型由你新建并负责维护。新建的条目不接收官方同步。</DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="grid grid-cols-[6rem_1fr] items-center gap-x-4 gap-y-3 text-sm">
            <span className="font-medium">模型厂商</span>
            <Input value={maker} onChange={(e) => setMaker(e.target.value)} placeholder="如 bfl" />
            <span className="font-medium">官方模型名</span>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="如 flux-2-pro" />
            <span className="text-muted-foreground">条目 id</span>
            <span className={cn("font-mono text-xs", taken ? "text-destructive" : "text-subtle-foreground")}>
              {maker || name ? id : "模型厂商/官方模型名"}
              {taken && "（已存在）"}
            </span>
            <span className="font-medium">显示名</span>
            <Input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="默认与官方模型名相同" />
            <span className="font-medium">媒体类型</span>
            <Segmented label="媒体类型" value={media} onChange={(m) => {
              setMedia(m);
              setPreset(m === "text" ? "token" : m === "image" ? "image" : m === "video" ? "second" : "character");
            }} options={MEDIA_TYPES.map((m) => ({ value: m, label: MEDIA_LABEL[m] }))} />
            <span className="font-medium">计价方式</span>
            <div className="flex items-center gap-2">
              <Segmented label="计价方式" value={preset} onChange={setPreset} options={(Object.keys(PRICE_PRESET_LABEL) as PricePreset[]).map((p) => ({ value: p, label: PRICE_PRESET_LABEL[p] }))} />
              <SimpleSelect label="币种" value={currency} onChange={(v) => setCurrency(v as Currency)} options={[{ value: "USD", label: "USD" }, { value: "CNY", label: "CNY" }]} className="w-20" />
            </div>
            <span />
            <span className="text-xs text-subtle-foreground">只是起点：创建后可在价目表里加缓存、分档等计量项与维度。</span>
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
                maker: maker.trim().toLowerCase(),
                name: name.trim(),
                displayName: displayName.trim() || name.trim(),
                mediaType: media,
                capabilities: media === "text" ? ["text_generation"] : media === "image" ? ["text_to_image"] : media === "audio" ? ["text_to_speech"] : [],
                durations: media === "video" ? [5, 10] : [],
                resolutions: [],
                durationConstraints: {},
                maxOutputTokens: null,
                price: priceFromPreset(preset, currency),
                version: "user",
              };
              createEntry(data);
              onOpenChange(false);
              onCreated(id);
            }}
          >
            新建
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
