// PROTOTYPE — #2972 市场分区（铺满档）。「浏览」「我的分享」「设置」三块的三种排布：
// tabs：顶部 Tabs 平铺三块；aside：浏览为主，宽窗口右侧常驻边栏放分享与源状态，设置走 Sheet；
// rail：左侧筛选栏，分享与设置作为栏底入口。
// 「设置」区收纳原 MarketSourcesDialog、GitHub raw 代理前缀（原在默认模型页）、官方服务开关与实例标识（原在关于页）。
// 未推出的条目类型（提示词、风格）按大厅结论隐藏。

import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ExternalLink, GripVertical, MoreHorizontal, Plus, RefreshCw, Search, Settings2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { API } from "@/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { useFooterSlot } from "@/prototype/page-shell/shell";
import type { MarketEntry, MarketSourceInfo, MarketSubmission, OfficialServiceState } from "@/types";
import { CONTROL, SampleTag, SaveBar, StatusDot, Trunc, protoToast, useWidth } from "./bits";
import { useOnce } from "./data";
import { useGoSection } from "./EndpointRef";
import { SAMPLE_MARKET_ENTRIES, SAMPLE_SUBMISSIONS } from "./samples";
import { useAxis, useProto } from "./store";

type Media = "all" | "image" | "video";

function useMarketData() {
  const { samples } = useProto();
  const sources = useOnce(() => API.listMarketSources().then((r) => r.sources));
  const entries = useOnce(() => API.listMarketEntries().then((r) => r.entries));
  const subs = useOnce(() => API.listMarketSubmissions().then((r) => r.submissions));
  const official = useOnce(() => API.getOfficialService());
  const config = useOnce(() => API.getSystemConfig());
  const useSampleEntries = samples && (entries?.length ?? 0) === 0;
  const useSampleSubs = samples && (subs?.length ?? 0) === 0;
  return {
    sources: sources ?? [],
    entries: useSampleEntries ? SAMPLE_MARKET_ENTRIES : (entries ?? []),
    subs: useSampleSubs ? SAMPLE_SUBMISSIONS : (subs ?? []),
    sampleEntries: useSampleEntries,
    sampleSubs: useSampleSubs,
    official,
    proxy: config?.settings.market_github_proxy_prefix ?? "",
  };
}

export function MarketProto() {
  const mode = useAxis("market");
  const data = useMarketData();
  const [query, setQuery] = useState("");
  const [media, setMedia] = useState<Media>("all");
  const [onlyInstalled, setOnlyInstalled] = useState(false);
  const [hiddenSources, setHiddenSources] = useState<Set<number>>(new Set());
  const visible = data.entries.filter(
    (e) =>
      (e.name + e.description + e.author).toLowerCase().includes(query.trim().toLowerCase()) &&
      (media === "all" || e.media_type === media) &&
      (!onlyInstalled || e.installation) &&
      !hiddenSources.has(e.source_id),
  );
  const filters = { query, setQuery, media, setMedia, onlyInstalled, setOnlyInstalled, hiddenSources, setHiddenSources };
  const grid = <EntryGrid entries={visible} sample={data.sampleEntries} total={data.entries.length} />;

  if (mode === "tabs") return <TabsLayout data={data} filters={filters} grid={grid} />;
  if (mode === "aside") return <AsideLayout data={data} filters={filters} grid={grid} />;
  return <RailLayout data={data} filters={filters} grid={grid} />;
}

type Data = ReturnType<typeof useMarketData>;
interface Filters {
  query: string;
  setQuery: (q: string) => void;
  media: Media;
  setMedia: (m: Media) => void;
  onlyInstalled: boolean;
  setOnlyInstalled: (v: boolean) => void;
  hiddenSources: Set<number>;
  setHiddenSources: (s: Set<number>) => void;
}

// ---------------------------------------------------------------------------
// 共用件
// ---------------------------------------------------------------------------

function SearchBox({ f, className }: { f: Filters; className?: string }) {
  return (
    <label className={cn("relative block", className)}>
      <span className="sr-only">搜索市场</span>
      <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-text-3" aria-hidden />
      <input type="search" value={f.query} onChange={(e) => f.setQuery(e.target.value)} placeholder="搜索名称、作者或说明" className={cn(CONTROL, "w-full pl-8")} />
    </label>
  );
}

function MediaFilter({ f }: { f: Filters }) {
  return (
    <ToggleGroup value={[f.media]} onValueChange={(v: string[]) => v[0] && f.setMedia(v[0] as Media)} variant="outline" size="sm" aria-label="媒体类型">
      <ToggleGroupItem value="all">全部</ToggleGroupItem>
      <ToggleGroupItem value="image">图片</ToggleGroupItem>
      <ToggleGroupItem value="video">视频</ToggleGroupItem>
    </ToggleGroup>
  );
}

function InstalledSwitch({ f }: { f: Filters }) {
  return (
    <label className="flex items-center gap-2 text-[13px] text-text-2">
      <Switch checked={f.onlyInstalled} onCheckedChange={f.setOnlyInstalled} />
      只看已安装
    </label>
  );
}

function sourceTone(s: MarketSourceInfo): "good" | "warn" | "muted" {
  if (!s.is_enabled) return "muted";
  return s.status === "ok" ? "good" : s.status === "never_fetched" ? "muted" : "warn";
}

function EntryCard({ e }: { e: MarketEntry }) {
  const goSection = useGoSection();
  const inst = e.installation;
  return (
    <article className="flex min-w-0 flex-col rounded-[10px] border border-border bg-card/60 p-4">
      <div className="flex items-start gap-3">
        <span className="inline-flex size-10 shrink-0 items-center justify-center rounded-md bg-muted text-[15px] font-medium text-text-2">{Array.from(e.name)[0]}</span>
        <div className="min-w-0 flex-1">
          <h3 className="line-clamp-2 text-[14px] leading-snug font-medium" title={e.name}>
            {e.name}
          </h3>
          <p className="mt-0.5 truncate text-[12px] text-text-3">
            {e.author}，v{e.version}
          </p>
        </div>
      </div>
      <p className="mt-3 line-clamp-2 min-h-[2lh] text-[13px] text-text-2" title={e.description ?? undefined}>
        {e.description}
      </p>
      <div className="mt-auto flex items-center gap-2 pt-3">
        <Badge variant="outline">{e.media_type === "video" ? "视频" : "图片"}</Badge>
        {!e.min_app_version_satisfied && <Badge variant="outline" className="border-warn/50 text-warn">需要 {e.min_app_version}</Badge>}
        <div className="ml-auto">
          {inst ? (
            inst.state === "update_available" ? (
              <Button size="sm">更新到 {e.version}</Button>
            ) : (
              <Button size="sm" variant="ghost" onClick={() => goSection("endpoints", { endpoint: inst.endpoint_key })}>
                已安装，打开
              </Button>
            )
          ) : (
            <Button size="sm" variant="outline" disabled={!e.min_app_version_satisfied}>
              安装
            </Button>
          )}
        </div>
      </div>
    </article>
  );
}

function EntryGrid({ entries, sample, total }: { entries: MarketEntry[]; sample: boolean; total: number }) {
  return (
    <div>
      <div className="mb-3 flex items-center gap-2 text-[13px] text-text-3">
        <span className="tabular-nums">
          {entries.length === total ? `${total} 个端点` : `${entries.length} / ${total} 个端点`}
        </span>
        {sample && <SampleTag />}
      </div>
      {entries.length === 0 ? (
        <p className="rounded-[10px] border border-dashed border-border px-4 py-12 text-center text-[13px] text-text-3">没有符合筛选条件的端点。</p>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-3">
          {entries.map((e) => (
            <EntryCard key={`${e.source_id}/${e.slug}`} e={e} />
          ))}
        </div>
      )}
    </div>
  );
}

function Submissions({ subs, sample, compact }: { subs: MarketSubmission[]; sample: boolean; compact?: boolean }) {
  const goSection = useGoSection();
  const status = (s: MarketSubmission) => (s.status === "merged" ? "已收录" : s.status === "open" ? "审核中" : "已关闭");
  if (subs.length === 0)
    return <p className="text-[13px] text-text-3">还没有分享过端点。在「调用端点」中打开自己的端点，选择「分享到市场」。</p>;
  return (
    <div>
      {sample && (
        <div className="mb-2">
          <SampleTag />
        </div>
      )}
      <ul className="divide-y divide-border rounded-[10px] border border-border">
        {subs.map((s) => (
          <li key={s.endpoint_id} className={cn("flex min-w-0 items-center gap-3 px-4", compact ? "py-2" : "py-2.5")}>
            <button type="button" onClick={() => goSection("endpoints", { endpoint: s.endpoint_key })} className="min-w-0 flex-1 text-left text-[13px] hover:text-primary">
              <Trunc text={s.endpoint_display_name} />
              {!compact && <span className="block truncate font-mono text-[12px] text-text-3">{s.slug}</span>}
            </button>
            <Badge variant={s.status === "merged" ? "secondary" : "outline"}>{status(s)}</Badge>
            {s.stale && !compact && <span className="text-[12px] text-text-3">状态可能已过期</span>}
            <a href={s.pr_url} target="_blank" rel="noreferrer" className="shrink-0 text-text-3 hover:text-text" aria-label={`在 GitHub 查看 ${s.endpoint_display_name} 的提交`}>
              <ExternalLink className="size-3.5" />
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

function SourceStatus({ s }: { s: MarketSourceInfo }) {
  const text = !s.is_enabled ? "已停用" : s.status === "ok" ? `${s.entry_count} 个条目` : s.status === "never_fetched" ? "尚未拉取" : "拉取失败";
  return (
    <span className="flex shrink-0 items-center gap-1.5 text-[12px] text-text-3">
      <StatusDot tone={sourceTone(s)} label={text} />
      {text}
    </span>
  );
}

/** 「设置」区：市场源、代理前缀、官方服务。源的增删改与官方服务开关即时生效；代理前缀是表单字段，走保存栏。 */
function MarketSettings({ data, footer }: { data: Data; footer: (bar: ReactNode) => ReactNode }) {
  const [proxy, setProxy] = useState<string | null>(null);
  const [official, setOfficial] = useState<OfficialServiceState | null>(null);
  const [resetOpen, setResetOpen] = useState(false);
  useEffect(() => {
    if (data.official) setOfficial(data.official);
  }, [data.official]);
  const proxyValue = proxy ?? data.proxy;
  const dirty = proxy !== null && proxy !== data.proxy;
  return (
    <div className="max-w-[760px] space-y-8">
      <section>
        <div className="mb-3 flex items-center gap-2">
          <h3 className="text-[15px] font-medium">市场源</h3>
          <Button variant="ghost" size="sm" className="ml-auto" onClick={() => protoToast("已刷新全部市场源")}>
            <RefreshCw />
            全部刷新
          </Button>
        </div>
        <ul className="divide-y divide-border rounded-[10px] border border-border">
          {data.sources.map((s) => (
            <li key={s.id} className="flex min-w-0 items-center gap-3 px-3 py-2.5">
              <GripVertical className="size-4 shrink-0 cursor-grab text-text-3" aria-label="拖动排序（也可用方向键）" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 text-[13px]">
                  <Trunc text={s.display_name} />
                  {s.kind === "official" && <Badge variant="secondary">官方</Badge>}
                </div>
                <Trunc text={s.address} mono className="text-[12px] text-text-3" />
              </div>
              <SourceStatus s={s} />
              <Switch defaultChecked={s.is_enabled} aria-label={`启用 ${s.display_name}`} />
              <DropdownMenu>
                <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label={`${s.display_name} 的更多操作`} />}>
                  <MoreHorizontal />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem>刷新</DropdownMenuItem>
                  <DropdownMenuItem>重命名</DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem variant="destructive" disabled={s.kind === "official"}>
                    删除{s.kind === "official" ? "（官方源不能删除）" : ""}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </li>
          ))}
          <li className="flex items-center gap-2 px-3 py-2.5">
            <input placeholder="GitHub 仓库（owner/repo）或索引文件地址" aria-label="新市场源地址" className={cn(CONTROL, "min-w-0 flex-1 font-mono")} />
            <Button variant="outline" onClick={() => protoToast("已添加市场源")}>
              <Plus />
              添加
            </Button>
          </li>
        </ul>
      </section>

      <section className="space-y-2">
        <h3 className="text-[15px] font-medium">GitHub 访问代理</h3>
        <p className="text-[13px] text-text-3">访问 GitHub 原始文件较慢时，在地址前加上代理前缀。留空则直连。</p>
        <input
          aria-label="GitHub raw 代理前缀"
          value={proxyValue}
          onChange={(e) => setProxy(e.target.value)}
          placeholder="https://proxy.example.com/"
          className={cn(CONTROL, "w-full font-mono")}
        />
      </section>

      <section className="space-y-3">
        <h3 className="text-[15px] font-medium">官方服务</h3>
        <p className="max-w-[40em] text-[13px] text-text-3">
          {official?.available
            ? "开启后，市场会显示评分与安装量，你也可以把自己的端点分享到官方市场。会随请求发送一个匿名的实例标识。"
            : "这个发行版没有配置官方服务地址，官方服务处于关闭状态。"}
        </p>
        {official?.available && (
          <>
            <label className="flex items-center gap-3 text-[13px]">
              <Switch
                checked={official.enabled}
                onCheckedChange={(v) => {
                  setOfficial({ ...official, enabled: v });
                  protoToast(v ? "已开启官方服务" : "已关闭官方服务");
                }}
              />
              使用官方服务
            </label>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px]">
              <span className="text-text-3">实例标识</span>
              <span className="font-mono break-all text-text-2">{official.instance_id ?? "尚未生成"}</span>
              <Button variant="ghost" size="sm" onClick={() => setResetOpen(true)}>
                重新生成
              </Button>
            </div>
          </>
        )}
      </section>

      {footer(<SaveBar dirty={dirty} onSave={() => protoToast("已保存代理前缀")} onDiscard={() => setProxy(null)} scope="代理前缀；市场源与官方服务开关即时生效" />)}

      <AlertDialog open={resetOpen} onOpenChange={setResetOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>重新生成实例标识？</AlertDialogTitle>
            <AlertDialogDescription>官方服务会把你当作一个新实例，之前的评分与分享记录不再与这台实例关联。</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => protoToast("已重新生成实例标识")}>
              重新生成
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/** 把保存栏渲染进外壳底行（限宽、铺满档的固定底行）。 */
function ShellFooter({ children }: { children: ReactNode }) {
  const slot = useFooterSlot();
  return slot ? createPortal(children, slot) : null;
}

// ---------------------------------------------------------------------------
// A — Tabs
// ---------------------------------------------------------------------------

function TabsLayout({ data, filters: f, grid }: { data: Data; filters: Filters; grid: ReactNode }) {
  const [tab, setTab] = useState("browse");
  return (
    <Tabs value={tab} onValueChange={setTab} className="gap-0">
      <div className="mb-5 flex flex-wrap items-center gap-3 border-b border-border">
        <TabsList variant="line" className="h-10">
          <TabsTrigger value="browse">浏览</TabsTrigger>
          <TabsTrigger value="shared">
            我的分享 <span className="text-text-3 tabular-nums">{data.subs.length}</span>
          </TabsTrigger>
          <TabsTrigger value="settings">设置</TabsTrigger>
        </TabsList>
      </div>
      <TabsContent value="browse">
        <div className="mb-5 flex flex-wrap items-center gap-3">
          <SearchBox f={f} className="w-72" />
          <MediaFilter f={f} />
          {data.sources.filter((s) => s.is_enabled).length > 1 && <SourceChips data={data} f={f} />}
          <div className="ml-auto">
            <InstalledSwitch f={f} />
          </div>
        </div>
        {grid}
      </TabsContent>
      <TabsContent value="shared">
        <div className="max-w-[760px]">
          <Submissions subs={data.subs} sample={data.sampleSubs} />
        </div>
      </TabsContent>
      <TabsContent value="settings">
        <MarketSettings data={data} footer={(bar) => <ShellFooter>{bar}</ShellFooter>} />
      </TabsContent>
    </Tabs>
  );
}

function SourceChips({ data, f }: { data: Data; f: Filters }) {
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="市场源">
      {data.sources
        .filter((s) => s.is_enabled)
        .map((s) => {
          const on = !f.hiddenSources.has(s.id);
          return (
            <button
              key={s.id}
              type="button"
              aria-pressed={on}
              onClick={() => {
                const n = new Set(f.hiddenSources);
                if (on) n.add(s.id);
                else n.delete(s.id);
                f.setHiddenSources(n);
              }}
              className={cn("rounded-full border px-2.5 py-1 text-[12px]", on ? "border-primary/40 bg-primary/12 text-text" : "border-border text-text-3")}
            >
              {s.display_name}
            </button>
          );
        })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// B — 浏览为主 + 右侧边栏
// ---------------------------------------------------------------------------

function AsideLayout({ data, filters: f, grid }: { data: Data; filters: Filters; grid: ReactNode }) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [ref, width] = useWidth<HTMLDivElement>();
  const side = width >= 1100;
  const aside = (
    <div className={cn("space-y-6", side ? "w-80 shrink-0" : "mt-10 grid grid-cols-1 gap-6 @[900px]:grid-cols-2")}>
      <section>
        <div className="mb-2 flex items-center">
          <h3 className="text-[14px] font-medium">市场源</h3>
          <Button variant="ghost" size="sm" className="ml-auto" onClick={() => setSettingsOpen(true)}>
            <Settings2 />
            市场设置
          </Button>
        </div>
        <ul className="space-y-1.5">
          {data.sources.map((s) => (
            <li key={s.id} className="flex min-w-0 items-center gap-2 text-[13px]">
              <span className="min-w-0 flex-1">
                <Trunc text={s.display_name} />
              </span>
              <SourceStatus s={s} />
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h3 className="mb-2 text-[14px] font-medium">我的分享</h3>
        <Submissions subs={data.subs} sample={data.sampleSubs} compact />
      </section>
    </div>
  );
  return (
    <div ref={ref} className="@container">
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <SearchBox f={f} className="w-72" />
        <MediaFilter f={f} />
        <div className="ml-auto">
          <InstalledSwitch f={f} />
        </div>
      </div>
      <div className={cn(side && "flex items-start gap-8")}>
        <div className="min-w-0 flex-1">{grid}</div>
        {aside}
      </div>
      <Sheet open={settingsOpen} onOpenChange={setSettingsOpen}>
        <SheetContent className="gap-0 p-0 data-[side=right]:w-[min(640px,90vw)] data-[side=right]:sm:max-w-none">
          <SheetHeader className="border-b border-border px-6 py-4">
            <SheetTitle>市场设置</SheetTitle>
            <SheetDescription>市场源、GitHub 访问代理与官方服务。</SheetDescription>
          </SheetHeader>
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
            <MarketSettings data={data} footer={(bar) => <div className="sticky bottom-0 -mx-6 border-t border-border bg-card px-6 py-3">{bar}</div>} />
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}

// ---------------------------------------------------------------------------
// C — 左侧筛选栏
// ---------------------------------------------------------------------------

function RailLayout({ data, filters: f, grid }: { data: Data; filters: Filters; grid: ReactNode }) {
  const [view, setView] = useState<"browse" | "shared" | "settings">("browse");
  const navItem = (id: typeof view, label: ReactNode) => (
    <button
      type="button"
      aria-current={view === id ? "page" : undefined}
      onClick={() => setView(id)}
      className={cn("flex h-8 w-full items-center gap-2 rounded-md px-2.5 text-left text-[13px]", view === id ? "bg-primary/14 text-text" : "text-text-2 hover:bg-accent")}
    >
      {label}
    </button>
  );
  return (
    <div className="flex items-start gap-8">
      <aside className="sticky top-0 w-56 shrink-0 space-y-5">
        {navItem("browse", "浏览端点")}
        {view === "browse" && (
          <div className="space-y-5 pl-2.5">
            <SearchBox f={f} />
            <div>
              <div className="mb-1.5 text-[12px] text-text-3">媒体类型</div>
              <MediaFilter f={f} />
            </div>
            <div>
              <div className="mb-1.5 text-[12px] text-text-3">市场源</div>
              <ul className="space-y-1">
                {data.sources
                  .filter((s) => s.is_enabled)
                  .map((s) => (
                    <li key={s.id}>
                      <label className="flex min-w-0 items-center gap-2 text-[13px]">
                        <input
                          type="checkbox"
                          checked={!f.hiddenSources.has(s.id)}
                          onChange={(e) => {
                            const n = new Set(f.hiddenSources);
                            if (e.target.checked) n.delete(s.id);
                            else n.add(s.id);
                            f.setHiddenSources(n);
                          }}
                          className="accent-[var(--color-primary)]"
                        />
                        <span className="min-w-0 flex-1">
                          <Trunc text={s.display_name} />
                        </span>
                        <StatusDot tone={sourceTone(s)} label={s.status} />
                      </label>
                    </li>
                  ))}
              </ul>
            </div>
            <InstalledSwitch f={f} />
          </div>
        )}
        <div className="border-t border-border pt-3">
          {navItem(
            "shared",
            <>
              我的分享 <span className="ml-auto text-text-3 tabular-nums">{data.subs.length}</span>
            </>,
          )}
          {navItem("settings", "设置")}
        </div>
      </aside>
      <div className="min-w-0 flex-1">
        {view === "browse" && grid}
        {view === "shared" && (
          <div className="max-w-[760px]">
            <h2 className="mb-3 text-[15px] font-medium">我的分享</h2>
            <Submissions subs={data.subs} sample={data.sampleSubs} />
          </div>
        )}
        {view === "settings" && <MarketSettings data={data} footer={(bar) => <ShellFooter>{bar}</ShellFooter>} />}
      </div>
    </div>
  );
}
