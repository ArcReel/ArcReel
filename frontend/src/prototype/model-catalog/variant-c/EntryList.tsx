// PROTOTYPE — #3091 变体 C 左栏：按模型厂商分组的条目列表。
// 每行是显示名加一排渠道小标（引用它的供应商），右侧是状态标记；底部单列「未对应条目的供应商模型」。
import { useMemo, useState } from "react";
import { ChevronDown, Plus, Search, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "cn";
import { MEDIA_LABEL, MEDIA_TYPES, effectiveOf, makerLabel, type MediaType } from "../model";
import { referencesOf, useProto } from "../store";
import { ChannelChip, FLAG_LABEL, FlagIcon, ProviderGlyph, SimpleSelect, flagsOf, type ListFlag } from "./shared";

type FlagFilter = "all" | ListFlag;
const MAKER_ORDER = ["google", "openai", "anthropic", "bytedance", "alibaba", "kuaishou", "minimax", "shengshu", "xai", "deepseek", "moonshot", "zhipu", "agnes", "bfl"];

export function EntryList({
  activeEntry,
  activeOrphan,
  providerFilter,
  onSelectEntry,
  onSelectOrphan,
  onClearProvider,
  onCreate,
  syncButton,
}: {
  activeEntry: string | null;
  activeOrphan: string | null;
  providerFilter: string | null;
  onSelectEntry: (id: string) => void;
  onSelectOrphan: (key: string) => void;
  onClearProvider: () => void;
  onCreate: () => void;
  syncButton?: React.ReactNode;
}) {
  const entries = useProto((s) => s.entries);
  const providers = useProto((s) => s.providers);
  const [query, setQuery] = useState("");
  const [usableOnly, setUsableOnly] = useState(true);
  const [media, setMedia] = useState<"all" | MediaType>("all");
  const [flag, setFlag] = useState<FlagFilter>("all");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const filterProvider = providers.find((p) => p.id === providerFilter) ?? null;

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return entries
      .map((e) => {
        const data = effectiveOf(e);
        const refs = referencesOf(providers, e.id);
        return { e, data, refs, flags: flagsOf(e) };
      })
      .filter(({ e, data, refs, flags }) => {
        if (filterProvider && !refs.some((r) => r.provider.id === filterProvider.id)) return false;
        if (!filterProvider && usableOnly && !refs.some((r) => r.provider.credentialCount > 0)) return false;
        if (media !== "all" && data.mediaType !== media) return false;
        if (flag !== "all" && !flags.includes(flag)) return false;
        if (q && !`${data.displayName} ${e.id} ${(data.aliases ?? []).join(" ")}`.toLowerCase().includes(q)) return false;
        return true;
      });
  }, [entries, providers, query, usableOnly, media, flag, filterProvider]);

  const groups = useMemo(() => {
    const byMaker = new Map<string, typeof rows>();
    for (const r of rows) {
      const list = byMaker.get(r.data.maker) ?? [];
      list.push(r);
      byMaker.set(r.data.maker, list);
    }
    const rank = (m: string) => (MAKER_ORDER.indexOf(m) === -1 ? 99 : MAKER_ORDER.indexOf(m));
    return [...byMaker.entries()].sort((a, b) => rank(a[0]) - rank(b[0]));
  }, [rows]);

  const orphans = providers.flatMap((p) => p.models.filter((m) => m.entryId === null).map((m) => ({ provider: p, model: m })));

  return (
    <nav aria-label="模型列表" data-zone="rail" className="flex w-72 shrink-0 flex-col border-r border-border min-[1280px]:w-80">
      <div className="flex shrink-0 flex-col gap-2.5 border-b border-border px-3 py-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="px-1 text-sm font-medium">模型</h2>
          <div className="flex items-center gap-1">
            {syncButton}
            <Button variant="ghost" size="icon-sm" aria-label="新建模型条目" title="新建模型条目" onClick={onCreate}>
              <Plus aria-hidden />
            </Button>
          </div>
        </div>
        <InputGroup>
          <InputGroupAddon>
            <Search aria-hidden />
          </InputGroupAddon>
          <InputGroupInput placeholder="搜索模型名或 id" aria-label="搜索模型" value={query} onChange={(e) => setQuery(e.target.value)} />
        </InputGroup>
        <ToggleGroup
          aria-label="媒体类型"
          variant="outline"
          size="sm"
          spacing={0}
          className="w-full"
          value={[media]}
          onValueChange={(next: string[]) => next[0] && setMedia(next[0] as "all" | MediaType)}
        >
          <ToggleGroupItem value="all" className="flex-1">
            全部
          </ToggleGroupItem>
          {MEDIA_TYPES.map((m) => (
            <ToggleGroupItem key={m} value={m} className="flex-1">
              {MEDIA_LABEL[m]}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <div className="flex items-center gap-3">
          <SimpleSelect<FlagFilter>
            size="sm"
            className="w-32 shrink-0"
            value={flag}
            onChange={setFlag}
            items={[{ value: "all", label: "全部状态" }, ...(Object.keys(FLAG_LABEL) as ListFlag[]).map((f) => ({ value: f, label: FLAG_LABEL[f] }))]}
          />
          {filterProvider ? (
            <div className="flex min-w-0 flex-1 items-center gap-1.5 rounded-md bg-muted py-0.5 pr-0.5 pl-2 text-xs">
              <ProviderGlyph provider={filterProvider} />
              <span className="min-w-0 flex-1 truncate" title={`只看 ${filterProvider.displayName} 提供的模型`}>
                {filterProvider.displayName}
              </span>
              <Button variant="ghost" size="icon-xs" aria-label="清除供应商筛选" onClick={onClearProvider}>
                <X aria-hidden />
              </Button>
            </div>
          ) : (
            <label className="flex min-w-0 flex-1 items-center justify-end gap-2 text-sm">
              <span className="truncate text-subtle-foreground">只看我能用的</span>
              <Switch size="sm" checked={usableOnly} onCheckedChange={setUsableOnly} />
            </label>
          )}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2 [scrollbar-gutter:stable]">
        {groups.length === 0 && <p className="px-2 py-6 text-center text-sm text-muted-foreground">没有符合条件的模型。</p>}
        {groups.map(([maker, list]) => {
          const open = !collapsed.has(maker);
          return (
            <Collapsible
              key={maker}
              open={open}
              onOpenChange={(v: boolean) =>
                setCollapsed((prev) => {
                  const next = new Set(prev);
                  if (v) next.delete(maker);
                  else next.add(maker);
                  return next;
                })
              }
              className="mb-1"
            >
              <CollapsibleTrigger className="flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-xs text-muted-foreground hover:bg-muted/60">
                <ChevronDown aria-hidden className={cn("size-3.5 transition-transform", !open && "-rotate-90")} />
                <span className="flex-1">{makerLabel(maker)}</span>
                <span className="tabular-nums">{list.length}</span>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <ul className="flex flex-col">
                  {list.map(({ e, data, refs, flags }) => {
                    const dup = list.some((o) => o.e.id !== e.id && o.data.displayName === data.displayName);
                    return (
                    <li key={e.id}>
                      <button
                        type="button"
                        aria-current={activeEntry === e.id ? "true" : undefined}
                        onClick={() => onSelectEntry(e.id)}
                        className={cn(
                          "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-muted/60",
                          activeEntry === e.id && "bg-muted hover:bg-muted",
                        )}
                      >
                        <span className="flex min-w-0 flex-1 flex-col gap-1">
                          <span className={cn("truncate text-sm", data.deprecated && "text-muted-foreground line-through decoration-muted-foreground/50")}>
                            {data.displayName}
                            {dup && <span className="ml-1.5 font-mono text-xs text-muted-foreground">{data.name}</span>}
                          </span>
                          <span className="flex min-h-5 flex-wrap items-center gap-1">
                            {refs.length === 0 ? (
                              <span className="text-xs text-muted-foreground">没有渠道</span>
                            ) : (
                              refs.slice(0, 6).map((r) => <ChannelChip key={r.model.key} provider={r.provider} model={r.model} />)
                            )}
                            {refs.length > 6 && <span className="text-xs text-muted-foreground">+{refs.length - 6}</span>}
                          </span>
                        </span>
                        <span className="flex shrink-0 items-center gap-1">
                          {flags.map((f) => (
                            <FlagIcon key={f} flag={f} />
                          ))}
                        </span>
                      </button>
                    </li>
                    );
                  })}
                </ul>
              </CollapsibleContent>
            </Collapsible>
          );
        })}

        {orphans.length > 0 && (
          <div className="mt-3 border-t border-border pt-3">
            <p className="px-2 pb-1 text-xs text-muted-foreground">未对应条目的供应商模型 · {orphans.length}</p>
            <ul>
              {orphans.map(({ provider, model }) => (
                <li key={model.key}>
                  <button
                    type="button"
                    aria-current={activeOrphan === model.key ? "true" : undefined}
                    onClick={() => onSelectOrphan(model.key)}
                    className={cn("flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-muted/60", activeOrphan === model.key && "bg-muted hover:bg-muted")}
                  >
                    <ProviderGlyph provider={provider} />
                    <span className="min-w-0 flex-1 truncate font-mono text-xs">{model.apiModelName}</span>
                    <FlagIcon flag="unpriced" />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </nav>
  );
}
