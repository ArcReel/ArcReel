// PROTOTYPE — 项目大厅（#2979）。「三个大厅形态 + 现状，同一路由 /app/projects，?variant= 切换。」
//
// 三个形态共用结论已定的结构（顶栏 → 问候区 → 吸顶工具栏 → 项目网格；铺满档、无侧栏；
// 外壳根 h-dvh、文档不滚动、主体是唯一滚动容器）。它们分歧的是项目卡的形态与问候区的占高：
//   B 海报卡：海报在上、信息在下，最接近现状的精简版
//   C 横排紧凑卡：缩略图在左、信息在右，一张卡约 104px 高，首屏放得下最多项目
//   D 海报铺满卡：整张卡就是海报，信息叠在海报底部渐变上
// 写操作（新建、导入、重命名、导出、删除）全部是桩，只弹提示。
// 「最近活动」时间是假数据：后端还没有 last_activity_at，这里按项目名哈希出一个时间并按它排序。

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useLocation } from "wouter";
import {
  Bot,
  ChevronDown,
  Clapperboard,
  Download,
  FileArchive,
  Library,
  MoreHorizontal,
  Pencil,
  Plus,
  Search,
  Settings,
  Trash2,
  Upload,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { Button } from "@/components/ui/button";
import { ButtonGroup } from "@/components/ui/button-group";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Typewriter } from "@/components/ui/Typewriter";
import { hashHue } from "@/components/ui/darkroom-tokens";
import { getProjectDisplayName } from "@/utils/project-display";
import { ONBOARDING_ANCHORS } from "@/onboarding/anchors";
import { BRAND } from "@/branding";
import { ExternalAgentModal } from "@/components/pages/ExternalAgentModal";
import { CreateProjectModal } from "@/components/pages/CreateProjectModal";
import { rememberAssetLibraryReturnTo } from "@/components/pages/AssetLibraryPage";
import { asProjectStatus, projectProgress, repairReasonOf, type ProjectProgress } from "@/components/pages/ProjectCard";
import type { ProjectStatus, ProjectSummary } from "@/types";
import { setProtoParam, useProtoParams, type LobbyVariant } from "./proto-params";
import { ProtoWizard } from "./ProtoWizard";

const stub = (action: string) => useAppStore.getState().pushToast(`原型：「${action}」不会写入数据`, "info");

// ---------------------------------------------------------------------------
// 数据：真实项目列表 + 假的「最近活动」时间
// ---------------------------------------------------------------------------

interface LobbyProject {
  p: ProjectSummary;
  status: ProjectStatus | null;
  progress: ProjectProgress;
  title: string;
  styleLabel: string | null;
  lastActivity: number;
}

function fakeLastActivity(name: string): number {
  // 5 分钟到 40 天前，按名字稳定
  const h = hashHue(name, 7) * 997 + hashHue(name, 3);
  const minutes = 5 + (h % (40 * 24 * 60));
  return Date.now() - minutes * 60_000;
}

const rtf = new Intl.RelativeTimeFormat("zh", { numeric: "auto" });
function relTime(ts: number): string {
  const min = Math.round((Date.now() - ts) / 60_000);
  if (min < 60) return rtf.format(-min, "minute");
  const hr = Math.round(min / 60);
  if (hr < 24) return rtf.format(-hr, "hour");
  const day = Math.round(hr / 24);
  if (day < 30) return rtf.format(-day, "day");
  return rtf.format(-Math.round(day / 30), "month");
}

type LobbyFilter = "all" | "in_progress" | "completed" | "repair";
const FILTER_LABEL: Record<LobbyFilter, string> = { all: "全部", in_progress: "进行中", completed: "已完成", repair: "待修复" };

function matches(lp: LobbyProject, f: LobbyFilter): boolean {
  if (f === "all") return true;
  if (!lp.status) return false;
  if (f === "repair") return lp.status.needs_repair;
  return f === "completed" ? lp.progress === "completed" : lp.progress !== "completed";
}

function useLobbyProjects() {
  const { t } = useTranslation(["dashboard", "templates"]);
  const { projects, projectsLoading, setProjects, setProjectsLoading } = useProjectsStore();
  useEffect(() => {
    setProjectsLoading(true);
    API.listProjects()
      .then((res) => setProjects(res.projects))
      .finally(() => setProjectsLoading(false));
  }, [setProjects, setProjectsLoading]);

  const list = useMemo<LobbyProject[]>(
    () =>
      projects
        .map((p) => {
          const status = asProjectStatus(p.status);
          return {
            p,
            status,
            progress: projectProgress(status),
            title: getProjectDisplayName(p.title, t("dashboard:untitled_project")),
            styleLabel: p.style_template_id
              ? t(`templates:name.${p.style_template_id}`)
              : p.style_image
                ? t("dashboard:style_custom")
                : null,
            lastActivity: fakeLastActivity(p.name),
          };
        })
        .sort((a, b) => b.lastActivity - a.lastActivity),
    [projects, t],
  );
  return { list, loading: projectsLoading };
}

// ---------------------------------------------------------------------------
// 共用小件：顶栏、分体按钮、问候区、工具栏、卡片零件、卡片菜单
// ---------------------------------------------------------------------------

function SplitCreateButton({ onCreate, onImport }: { onCreate: () => void; onImport: () => void }) {
  return (
    <ButtonGroup>
      <Button onClick={onCreate} data-onboarding={ONBOARDING_ANCHORS.lobbyCreateProject}>
        <Plus data-icon="inline-start" />
        新建项目
      </Button>
      <DropdownMenu>
        <DropdownMenuTrigger render={<Button size="icon" aria-label="更多新建方式" className="border-l border-l-primary-foreground/20" />}>
          <ChevronDown />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-40">
          <DropdownMenuItem onClick={onImport}>
            <FileArchive />
            导入 ZIP…
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </ButtonGroup>
  );
}

function TopBar({ query, onQuery, onCreate, onImport, onExternal }: {
  query: string;
  onQuery: (v: string) => void;
  onCreate: () => void;
  onImport: () => void;
  onExternal: () => void;
}) {
  const [, navigate] = useLocation();
  const configComplete = useConfigStatusStore((s) => s.isComplete);
  const searchRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);
  return (
    // 与设置页外壳同一顶栏：横跨全宽、高 56px、标题组靠左、页面动作靠右
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border pl-4 pr-6 xl:pr-8">
      <div className="flex shrink-0 items-center gap-2">
        <img src="/logo.svg" alt="" className="h-7 w-7" />
        <span className="text-[17px] font-medium">{BRAND.name}</span>
      </div>
      <label className="ml-3 flex h-8 w-[min(320px,30vw)] items-center gap-2 rounded-lg border border-input bg-input/30 px-2.5 focus-within:border-ring">
        <Search aria-hidden className="size-4 text-muted-foreground" />
        <input
          ref={searchRef}
          type="search"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          aria-label="搜索项目"
          placeholder="搜索项目"
          className="min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-muted-foreground"
        />
        <kbd className="text-[11px] text-muted-foreground">⌘K</kbd>
      </label>
      <div className="ml-auto flex items-center gap-1.5">
        <Button
          variant="ghost"
          onClick={() => {
            rememberAssetLibraryReturnTo(window.location.pathname);
            navigate("/app/assets");
          }}
        >
          <Library data-icon="inline-start" />
          资产库
        </Button>
        <Button variant="ghost" size="icon" aria-label="外部 Agent 接入" title="外部 Agent 接入" onClick={onExternal}>
          <Bot />
        </Button>
        <span aria-hidden className="mx-1 h-5 w-px bg-border" />
        <SplitCreateButton onCreate={onCreate} onImport={onImport} />
        <Button
          variant="ghost"
          size="icon"
          aria-label="设置"
          title="设置"
          className="relative"
          data-onboarding={ONBOARDING_ANCHORS.lobbySettings}
          onClick={() => navigate("/app/settings")}
        >
          <Settings />
          {!configComplete && <span aria-label="配置未完成" className="absolute top-1 right-1 size-2 rounded-full bg-warm-bright" />}
        </Button>
      </div>
    </header>
  );
}

function greetingOf(d = new Date()) {
  const h = d.getHours();
  if (h >= 5 && h < 11) return "早上好，导演。";
  if (h >= 11 && h < 14) return "中午好，导演。";
  if (h >= 14 && h < 22) return "晚上好，导演。";
  return "夜深了，导演。";
}

function Greeting({ list, inline }: { list: LobbyProject[]; inline?: boolean }) {
  const inProgress = list.filter((x) => matches(x, "in_progress")).length;
  let done = 0;
  let making = 0;
  for (const x of list) {
    done += x.status?.episodes_summary.completed ?? 0;
    making += x.status?.episodes_summary.in_production ?? 0;
  }
  const status = list.length === 0 ? "从第一部作品开始吧。" : inProgress > 0 ? `${inProgress} 部作品正在制作中。` : "所有作品都已完成。";
  const summary = list.length === 0 ? null : `已完成 ${done} 集，${making} 集还在制作`;
  return (
    <div className={cn("px-6 pt-7 pb-5 xl:px-8", inline && "flex flex-wrap items-baseline gap-x-5 gap-y-1 pt-6 pb-4")}>
      <h1 className="font-editorial m-0 text-[32px] leading-[1.2] font-normal tracking-[-0.01em]">
        <Typewriter
          once="proto-lobby-hero"
          segments={[{ text: greetingOf() }, { text: status, style: { color: "var(--color-primary)" } }]}
        />
      </h1>
      {summary && <p className={cn("m-0 text-[13px] text-muted-foreground", !inline && "mt-1.5")}>{summary}</p>}
    </div>
  );
}

function Toolbar({ filter, onFilter, counts }: { filter: LobbyFilter; onFilter: (f: LobbyFilter) => void; counts: Record<LobbyFilter, number> }) {
  return (
    // 吸顶在滚动容器顶端；不透明底色，不用 backdrop-blur
    <div className="sticky top-0 z-10 border-b border-border bg-background/95">
      <div className="flex h-12 items-center gap-1 px-6 xl:px-8">
        {(Object.keys(FILTER_LABEL) as LobbyFilter[]).map((f) => {
          const active = filter === f;
          return (
            <button
              key={f}
              type="button"
              aria-pressed={active}
              onClick={() => onFilter(f)}
              className={cn(
                "inline-flex h-7 items-center gap-1.5 rounded-full px-3 text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                active ? "bg-primary/16 text-foreground" : "text-text-2 [@media(hover:hover)]:hover:bg-accent [@media(hover:hover)]:hover:text-foreground",
              )}
            >
              {FILTER_LABEL[f]}
              <span className={cn("tabular-nums", active ? "text-primary" : "text-muted-foreground")}>{counts[f]}</span>
            </button>
          );
        })}
        <span className="ml-auto text-[12px] text-muted-foreground">按最近活动排序</span>
      </div>
    </div>
  );
}

function PosterArt({ lp, className, children, titleOnArt }: { lp: LobbyProject; className?: string; children?: ReactNode; titleOnArt?: boolean }) {
  const hue = hashHue(lp.p.name, 17);
  return (
    <div
      className={cn("relative overflow-hidden", className)}
      style={{
        background: `radial-gradient(120% 80% at 30% 30%, oklch(0.55 0.15 ${hue}) 0%, oklch(0.28 0.08 ${(hue + 10) % 360}) 45%, oklch(0.14 0.02 265) 100%)`,
      }}
    >
      {lp.p.thumbnail ? (
        <img src={lp.p.thumbnail} alt="" loading="lazy" decoding="async" className="absolute inset-0 size-full object-cover" />
      ) : titleOnArt ? (
        // 没有缩略图时用标题当海报字，避免一块无意义的色块
        <div className="font-editorial absolute inset-x-3 bottom-2.5 line-clamp-2 text-[22px] leading-[1.05] text-white/90">{lp.title}</div>
      ) : null}
      {children}
    </div>
  );
}

function StyleBadge({ lp }: { lp: LobbyProject }) {
  const { tag } = useProtoParams();
  if (tag === "none" || !lp.styleLabel) return null;
  return (
    <span className="absolute top-2 left-2 max-w-[70%] truncate rounded-md bg-black/55 px-1.5 py-0.5 text-[11px] text-white/90">
      {lp.styleLabel}
    </span>
  );
}

const PROGRESS_TEXT: Record<ProjectProgress, string> = { empty: "尚未建集", in_progress: "制作中", completed: "已完成" };
const PROGRESS_DOT: Record<ProjectProgress, string> = { empty: "bg-text-3", in_progress: "bg-primary", completed: "bg-good" };

function ProgressLine({ lp }: { lp: LobbyProject }) {
  return (
    <div className="flex min-w-0 items-center gap-2 text-[12px]">
      <span className="inline-flex items-center gap-1.5 text-text-2">
        <span aria-hidden className={cn("size-1.5 rounded-full", PROGRESS_DOT[lp.progress])} />
        {PROGRESS_TEXT[lp.progress]}
      </span>
      {lp.status?.needs_repair && (
        <span className="rounded-full bg-warm-soft px-1.5 py-px text-[11px] text-warm">待修复</span>
      )}
    </div>
  );
}

function EpisodeStrip({ lp, className }: { lp: LobbyProject; className?: string }) {
  const s = lp.status?.episodes_summary;
  if (!s || s.total === 0) return null;
  return (
    <div aria-hidden className={cn("flex gap-[3px]", className)}>
      {Array.from({ length: s.total }).map((_, i) => (
        <span
          key={i}
          className={cn(
            "h-[3px] flex-1 rounded-full",
            i < s.completed ? "bg-good" : i < s.completed + s.in_production ? "bg-primary" : i < s.completed + s.in_production + s.scripted ? "bg-text-3" : "bg-muted",
          )}
        />
      ))}
    </div>
  );
}

/** 「已完成 2 / 5 集 · 3 小时前更新」：集数并进这一行，进度行就不必再重复「N 集」。 */
function metaLine(lp: LobbyProject): string {
  const s = lp.status?.episodes_summary;
  const ep = !s || s.total === 0 ? "还没有集" : lp.progress === "completed" ? `${s.total} 集` : `已完成 ${s.completed} / ${s.total} 集`;
  return `${ep} · ${relTime(lp.lastActivity)}更新`;
}

function RepairReason({ lp, className }: { lp: LobbyProject; className?: string }) {
  const r = repairReasonOf(lp.status);
  if (!r) return null;
  return <p className={cn("line-clamp-2 text-[12px] leading-[1.45] text-muted-foreground", className)}>{r}</p>;
}

interface CardActions {
  onRename: (lp: LobbyProject) => void;
  onDelete: (lp: LobbyProject) => void;
}

function CardMenu({ lp, actions, className }: { lp: LobbyProject; actions: CardActions; className?: string }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="secondary"
            size="icon-sm"
            aria-label={`${lp.title}：更多操作`}
            className={cn(
              "bg-black/55 text-white hover:bg-black/70 aria-expanded:bg-black/70 aria-expanded:opacity-100",
              // 有悬停能力的设备平时隐藏，悬停或聚焦时出现；触屏设备常驻
              "[@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover:opacity-100 focus-visible:opacity-100 [@media(hover:hover)]:group-focus-within:opacity-100",
              className,
            )}
          />
        }
      >
        <MoreHorizontal />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-36">
        <DropdownMenuItem onClick={() => actions.onRename(lp)}>
          <Pencil />
          重命名
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => stub("导出（复用工作区的导出范围对话框）")}>
          <Download />
          导出
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" onClick={() => actions.onDelete(lp)}>
          <Trash2 />
          删除
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

const CARD_SHELL =
  "group relative overflow-hidden rounded-xl border border-border bg-card transition-colors [@media(hover:hover)]:hover:border-primary/40 focus-within:border-primary/60";

// ---------------------------------------------------------------------------
// 三种项目卡
// ---------------------------------------------------------------------------

/** B 海报卡：海报 2:1 在上，信息在下。 */
function PosterCard({ lp, actions }: { lp: LobbyProject; actions: CardActions }) {
  return (
    <article className={CARD_SHELL}>
      <Link href={`/app/projects/${lp.p.name}`} className="block text-foreground no-underline outline-none" aria-label={lp.title}>
        <PosterArt lp={lp} className="aspect-[2/1]" titleOnArt>
          <StyleBadge lp={lp} />
        </PosterArt>
        <div className="space-y-2 px-3.5 pt-3 pb-3.5">
          <h3 className="truncate text-[15px] font-medium">{lp.title}</h3>
          <ProgressLine lp={lp} />
          <RepairReason lp={lp} />
          <EpisodeStrip lp={lp} />
          <p className="text-[12px] text-muted-foreground">{metaLine(lp)}</p>
        </div>
      </Link>
      <CardMenu lp={lp} actions={actions} className="absolute top-2 right-2" />
    </article>
  );
}

/** C 横排紧凑卡：缩略图在左，信息在右。 */
function RowCard({ lp, actions }: { lp: LobbyProject; actions: CardActions }) {
  return (
    <article className={CARD_SHELL}>
      <Link href={`/app/projects/${lp.p.name}`} className="flex gap-3 p-2.5 text-foreground no-underline outline-none" aria-label={lp.title}>
        <PosterArt lp={lp} className="aspect-[4/3] w-[128px] shrink-0 rounded-lg">
          <StyleBadge lp={lp} />
        </PosterArt>
        <div className="flex min-w-0 flex-1 flex-col gap-1.5 py-0.5 pr-8">
          <h3 className="truncate text-[15px] font-medium">{lp.title}</h3>
          <ProgressLine lp={lp} />
          <RepairReason lp={lp} className="line-clamp-1" />
          <div className="mt-auto space-y-1.5">
            <EpisodeStrip lp={lp} />
            <p className="truncate text-[12px] text-muted-foreground">{metaLine(lp)}</p>
          </div>
        </div>
      </Link>
      <CardMenu lp={lp} actions={actions} className="absolute top-2 right-2 bg-transparent text-text-2 hover:bg-accent" />
    </article>
  );
}

/** D 海报铺满卡：整张卡是海报，信息叠在底部渐变上。 */
function FullPosterCard({ lp, actions }: { lp: LobbyProject; actions: CardActions }) {
  return (
    <article className={cn(CARD_SHELL, "bg-black")}>
      <Link href={`/app/projects/${lp.p.name}`} className="block text-white no-underline outline-none" aria-label={lp.title}>
        <PosterArt lp={lp} className="aspect-[4/3]">
          <StyleBadge lp={lp} />
          {lp.status?.needs_repair && (
            <span className="absolute top-2 right-11 rounded-md bg-warm px-1.5 py-0.5 text-[11px] text-black">待修复</span>
          )}
          <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/90 via-black/60 to-transparent px-3.5 pt-10 pb-3">
            <h3 className="truncate text-[16px] font-medium">{lp.title}</h3>
            <p className="mt-0.5 truncate text-[12px] text-white/70">
              {PROGRESS_TEXT[lp.progress]} · {metaLine(lp)}
            </p>
            <RepairReason lp={lp} className="mt-1 text-white/70" />
          </div>
          <EpisodeStrip lp={lp} className="absolute inset-x-0 bottom-0 gap-px [&>span]:rounded-none" />
        </PosterArt>
      </Link>
      <CardMenu lp={lp} actions={actions} className="absolute top-2 right-2" />
    </article>
  );
}

const GRID: Record<Exclude<LobbyVariant, "A">, string> = {
  // 列数由网格实际宽度决定（铺满档），宽窗口自动加列
  B: "grid-cols-[repeat(auto-fill,minmax(280px,1fr))]",
  C: "grid-cols-[repeat(auto-fill,minmax(380px,1fr))]",
  D: "grid-cols-[repeat(auto-fill,minmax(300px,1fr))]",
};

// ---------------------------------------------------------------------------
// 页面
// ---------------------------------------------------------------------------

export function ProtoLobby({ variant }: { variant: Exclude<LobbyVariant, "A"> }) {
  const params = useProtoParams();
  const { list: realList, loading } = useLobbyProjects();
  const list = params.empty ? [] : realList;
  const { showCreateModal, setShowCreateModal } = useProjectsStore();
  const [filter, setFilter] = useState<LobbyFilter>("all");
  const [query, setQuery] = useState("");
  const [renaming, setRenaming] = useState<LobbyProject | null>(null);
  const [deleting, setDeleting] = useState<LobbyProject | null>(null);
  const [external, setExternal] = useState(false);

  const counts = useMemo(() => {
    const out: Record<LobbyFilter, number> = { all: 0, in_progress: 0, completed: 0, repair: 0 };
    for (const x of list) for (const f of Object.keys(out) as LobbyFilter[]) if (matches(x, f)) out[f] += 1;
    return out;
  }, [list]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return list.filter((x) => matches(x, filter) && (!q || `${x.title} ${x.p.name}`.toLowerCase().includes(q)));
  }, [list, filter, query]);

  const openWizard = () => (params.wizard === "A" ? setShowCreateModal(true) : setProtoParam("open", "1"));
  const onImport = () => stub("导入 ZIP");
  const actions: CardActions = { onRename: setRenaming, onDelete: setDeleting };
  const Card = variant === "B" ? PosterCard : variant === "C" ? RowCard : FullPosterCard;

  return (
    <div className="relative flex h-dvh flex-col overflow-hidden bg-[linear-gradient(180deg,var(--color-bg-grad-a),var(--color-bg-grad-b))] text-foreground">
      <TopBar query={query} onQuery={setQuery} onCreate={openWizard} onImport={onImport} onExternal={() => setExternal(true)} />

      <main data-scroll-owner className="relative min-h-0 flex-1 overflow-y-auto [scrollbar-gutter:stable]">
        <Greeting list={list} inline={variant === "C"} />

        {loading && !params.empty ? (
          <p className="px-8 py-16 text-center text-muted-foreground">正在加载项目…</p>
        ) : list.length === 0 ? (
          <Empty className="mx-6 mb-10 w-auto border border-border py-16 xl:mx-8">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Clapperboard />
              </EmptyMedia>
              <EmptyTitle>还没有项目</EmptyTitle>
              <EmptyDescription>从一部小说、一个剧本或一个想法开始。之前导出的项目可以用 ZIP 导入。</EmptyDescription>
            </EmptyHeader>
            <EmptyContent className="flex-row justify-center">
              <Button onClick={openWizard}>
                <Plus data-icon="inline-start" />
                新建项目
              </Button>
              <Button variant="outline" onClick={onImport}>
                <Upload data-icon="inline-start" />
                导入 ZIP
              </Button>
            </EmptyContent>
          </Empty>
        ) : (
          <>
            <Toolbar filter={filter} onFilter={setFilter} counts={counts} />
            {shown.length === 0 ? (
              <div className="px-8 py-16 text-center text-muted-foreground">
                <p className="text-foreground">没有符合条件的项目</p>
                <Button
                  variant="outline"
                  className="mt-3"
                  onClick={() => {
                    setFilter("all");
                    setQuery("");
                  }}
                >
                  清除筛选
                </Button>
              </div>
            ) : (
              <div className={cn("grid gap-4 px-6 pt-5 pb-24 xl:px-8", GRID[variant])}>
                {shown.map((lp) => (
                  <Card key={lp.p.name} lp={lp} actions={actions} />
                ))}
              </div>
            )}
          </>
        )}
      </main>

      <RenameDialog lp={renaming} onClose={() => setRenaming(null)} />
      <AlertDialog open={!!deleting} onOpenChange={(o) => !o && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除「{deleting?.title}」？</AlertDialogTitle>
            <AlertDialogDescription>项目的脚本、资产与所有生成产物都会被永久删除，无法恢复。</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                stub("删除项目");
                setDeleting(null);
              }}
            >
              删除项目
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {external && <ExternalAgentModal onClose={() => setExternal(false)} />}
      {showCreateModal && <CreateProjectModal />}
      {params.open && params.wizard !== "A" && (
        <ProtoWizard variant={params.wizard} height={params.wizardHeight} onClose={() => setProtoParam("open", null)} />
      )}
    </div>
  );
}

function RenameDialog({ lp, onClose }: { lp: LobbyProject | null; onClose: () => void }) {
  const [value, setValue] = useState("");
  useEffect(() => {
    if (lp) setValue(lp.p.title);
  }, [lp]);
  return (
    <Dialog open={!!lp} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>重命名项目</DialogTitle>
          <DialogDescription>项目 ID「{lp?.p.name}」不随标题变化。</DialogDescription>
        </DialogHeader>
        <Input value={value} onChange={(e) => setValue(e.target.value)} aria-label="项目标题" autoFocus />
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button
            disabled={!value.trim()}
            onClick={() => {
              stub("重命名");
              onClose();
            }}
          >
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
