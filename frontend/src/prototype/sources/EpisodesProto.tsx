// PROTOTYPE — 原文与分集视图原型（#2982）的分集视图部件，不合并。
// 页头工具行、未登记文件提示条、集目录（outline）、分集清单表格（tabs）、单集操作菜单、模拟的重新规划候选。
// 文案直接写中文，没有走 i18n。

import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "wouter";
import {
  ArrowUpRight,
  ChevronDown,
  Combine,
  FilePlus,
  FileText,
  ListX,
  Loader2,
  MoreHorizontal,
  Plus,
  RefreshCw,
  Sparkles,
  Trash2,
  TriangleAlert,
  Upload,
} from "lucide-react";

import { EPISODE_PLANNING_SLOTS } from "@/actions/generation";
import { WORKSPACE_ROUTE_EPISODES } from "@/app-routes";
import { EpisodePlanningPanel } from "@/components/canvas/episodes/EpisodePlanningPanel";
import { PlanGapButton } from "@/components/canvas/episodes/PlanGapButton";
import { ReplannedBadge } from "@/components/canvas/episodes/ReplannedBadge";
import { UnregisteredFilesPanel } from "@/components/canvas/episodes/UnregisteredFilesPanel";
import { remainingUnits } from "@/components/canvas/episodes/episode-planning-model";
import {
  episodeColor,
  formatSpoken,
  formatVolume,
  otherEpisodes,
  railFileGroups,
  type SourceUploadMode,
} from "@/components/canvas/episodes/episodes-view-model";
import { cutEpisodeActions } from "@/components/canvas/episodes/manual-split-model";
import { Alert, AlertAction, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ButtonGroup } from "@/components/ui/button-group";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useActiveResourceIds } from "@/stores/tasks-store";
import type { EpisodeMeta, EpisodesView, EpisodesViewEpisode, ReplanSummary, ReplanCandidateEpisode } from "@/types";
import { episodePosition } from "@/utils/episode-display";

function usePlanning(projectName: string) {
  const active = useActiveResourceIds("text_episode_plan", projectName);
  return EPISODE_PLANNING_SLOTS.some((slot) => active.has(slot));
}

// ---------------------------------------------------------------------------
// 页头工具行：标题与集数、源文进度、AI 规划分集（Popover）、新建一集、上传原文（分体按钮）
// ---------------------------------------------------------------------------

export function EpisodesHeaderBar({
  projectName,
  view,
  episodes,
  onUpload,
  onCreate,
  children,
}: {
  projectName: string;
  view: EpisodesView;
  episodes: EpisodeMeta[];
  onUpload: (mode: SourceUploadMode) => void;
  onCreate: () => void;
  /** 标题右侧的附加内容（上下 tab 结构把 tab 放在这里）。 */
  children?: ReactNode;
}) {
  const { t } = useTranslation(["dashboard"]);
  const planning = usePlanning(projectName);
  const remaining = remainingUnits(view);
  const started = view.cut_units > 0;
  const percent = view.units === 0 ? 0 : Math.round((view.cut_units / view.units) * 100);
  const hasSource = view.files.length > 0;
  const planLabel = planning
    ? `AI 规划中 · ${percent}%`
    : view.replan !== null
      ? "有新的分集方案"
      : remaining === 0 && started
        ? "已全部分集"
        : started
          ? "AI 规划剩余内容"
          : "AI 规划分集";

  return (
    <div className="@container/ephb flex h-12 shrink-0 items-center gap-3 border-b border-border px-4">
      <h2 className="shrink-0 text-[15px] font-semibold text-foreground">分集</h2>
      <span className="shrink-0 text-[12px] tabular-nums text-muted-foreground">{episodes.length} 集</span>
      {children}
      {hasSource ? (
        <div className="flex min-w-0 items-center gap-2 text-[12px] text-muted-foreground" title="整本源文里已经分到集里的字数">
          <span aria-hidden className="h-1 w-20 shrink-0 overflow-hidden rounded-full bg-muted">
            <span className="block h-full rounded-full bg-primary" style={{ width: `${percent}%` }} />
          </span>
          <span className="hidden truncate tabular-nums @min-[820px]/ephb:inline">
            已分集 {view.cut_units.toLocaleString()} / {formatVolume(t, view.units, view.unit)}
          </span>
        </div>
      ) : null}
      <span className="flex-1" />
      {hasSource ? (
        <Popover>
          <PopoverTrigger
            render={
              <Button
                variant={remaining > 0 && !planning && view.replan === null ? "secondary" : "ghost"}
                size="sm"
                disabled={view.replan !== null}
                title={view.replan !== null ? "先采纳或放弃右栏的新方案" : undefined}
              />
            }
          >
            {planning ? <Loader2 className="motion-safe:animate-spin" /> : <Sparkles />}
            {planLabel}
            <ChevronDown />
          </PopoverTrigger>
          <PopoverContent align="end" className="w-[340px] p-3 [&_section]:border-t-0 [&_section]:pt-0">
            <EpisodePlanningPanel projectName={projectName} view={view} active={planning} />
          </PopoverContent>
        </Popover>
      ) : null}
      <Button variant="ghost" size="sm" onClick={onCreate}>
        <FilePlus />
        新建一集
      </Button>
      <ButtonGroup>
        <Button size="sm" onClick={() => onUpload("whole_source")}>
          <Upload />
          上传原文
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger render={<Button size="icon-sm" aria-label="选择上传方式" className="border-l border-l-primary-foreground/20" />}>
            <ChevronDown />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="min-w-48">
            <DropdownMenuItem onClick={() => onUpload("whole_source")}>整本源文</DropdownMenuItem>
            <DropdownMenuItem onClick={() => onUpload("episode")}>逐集原文（每个文件一集）</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </ButtonGroup>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 未登记文件：页头下方一行提示条，点「处理」在对话框里逐个处置
// ---------------------------------------------------------------------------

export function UnregisteredBanner({
  projectName,
  view,
  episodes,
  onChanged,
}: {
  projectName: string;
  view: EpisodesView;
  episodes: EpisodeMeta[];
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  if (view.unregistered.length === 0) return null;
  const names = view.unregistered.map((file) => file.name);
  return (
    <>
      <Alert className="mx-4 mt-3 w-auto shrink-0 py-2">
        <TriangleAlert />
        <AlertDescription className="truncate">
          原文目录里有 {names.length} 个文件没有登记：{names.slice(0, 2).join("、")}
          {names.length > 2 ? ` 等` : ""}。登记前不会参与分集。
        </AlertDescription>
        <AlertAction>
          <Button size="xs" variant="outline" onClick={() => setOpen(true)}>
            处理
          </Button>
        </AlertAction>
      </Alert>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>未登记的文件</DialogTitle>
            <DialogDescription>逐个决定：并入整本源文、作为某一集的原文，或删除。</DialogDescription>
          </DialogHeader>
          <div className="max-h-[60dvh] overflow-y-auto [&>section>header]:hidden">
            <UnregisteredFilesPanel projectName={projectName} files={view.unregistered} episodes={episodes} onChanged={onChanged} />
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

// ---------------------------------------------------------------------------
// 单集操作菜单：集目录与分集清单共用
// ---------------------------------------------------------------------------

export interface EpisodeMenuActions {
  busy: boolean;
  replanBlocked: string | null;
  onReplan: (episode: number) => void;
  onMergeWithNext: (episode: number) => void;
  onClearAfter: (episode: number) => void;
  onCreate: (after: number | null) => void;
  onDelete: (episode: number) => void;
}

export function EpisodeActionsMenu({
  view,
  episode,
  actions,
  label,
}: {
  view: EpisodesView;
  episode: number;
  actions: EpisodeMenuActions;
  label: string;
}) {
  const [, setLocation] = useLocation();
  const available = cutEpisodeActions(view, episode);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label={`${label}的操作`}
            className="opacity-0 group-hover/row:opacity-100 group-focus-within/row:opacity-100 aria-expanded:opacity-100"
            onClick={(event) => event.stopPropagation()}
          />
        }
      >
        <MoreHorizontal />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-52">
        <DropdownMenuItem onClick={() => setLocation(`/${WORKSPACE_ROUTE_EPISODES}/${episode}`)}>
          <ArrowUpRight />
          打开这一集
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => actions.onCreate(episode)}>
          <Plus />
          在后面新建一集
        </DropdownMenuItem>
        {available.placed ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem disabled={actions.busy || available.merge !== "ok"} onClick={() => actions.onMergeWithNext(episode)}>
              <Combine />
              与下一集合并
            </DropdownMenuItem>
            <DropdownMenuItem disabled={actions.busy || !available.clearAfter} onClick={() => actions.onClearAfter(episode)}>
              <ListX />
              清除之后的分集
            </DropdownMenuItem>
            <DropdownMenuItem disabled={actions.replanBlocked !== null} onClick={() => actions.onReplan(episode)}>
              <RefreshCw />
              从这一集开始重新规划
            </DropdownMenuItem>
          </>
        ) : null}
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" onClick={() => actions.onDelete(episode)}>
          <Trash2 />
          删除这一集
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// ---------------------------------------------------------------------------
// 集目录（outline）：左侧单行目录，跟随原文滚动高亮当前集；操作收进每行的「⋯」
// ---------------------------------------------------------------------------

export function EpisodeOutline({
  projectName,
  view,
  episodes,
  current,
  onLocate,
  onScrollToFile,
  actions,
}: {
  projectName: string;
  view: EpisodesView;
  episodes: EpisodeMeta[];
  current: number | null;
  onLocate: (episode: number) => void;
  onScrollToFile: (sourceFile: string) => void;
  actions: EpisodeMenuActions;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  void projectName;
  const groups = railFileGroups(view, episodes);
  const others = otherEpisodes(view, episodes);
  const multiFile = view.files.length > 1;

  const row = (episode: EpisodeMeta, info: EpisodesViewEpisode | null) => {
    const id = episode.episode;
    const position = episodePosition(episodes, id);
    const name = position === null ? "未排期" : `第 ${position} 集`;
    const active = current === id;
    return (
      <li key={id} className="group/row relative">
        <button
          type="button"
          onClick={() => onLocate(id)}
          aria-current={active ? "location" : undefined}
          className={`focus-ring flex w-full items-center gap-2 rounded-md py-1.5 pl-2.5 pr-8 text-left text-[12.5px] transition-colors ${
            active ? "bg-primary/12 text-foreground" : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"
          }`}
        >
          <span aria-hidden className="h-3.5 w-[3px] shrink-0 rounded-full" style={{ background: episodeColor(id) }} />
          <span className="w-5 shrink-0 text-right tabular-nums" style={{ color: active ? episodeColor(id) : undefined }}>
            {position ?? "—"}
          </span>
          <span className="min-w-0 flex-1 truncate">{episode.title?.trim() || "未命名"}</span>
          {episode.ledger_status === "stale" ? <ReplannedBadge /> : null}
          {info?.units != null ? (
            <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground/80 group-hover/row:invisible">
              {formatVolume(t, info.units, view.unit)}
            </span>
          ) : null}
        </button>
        <span className="absolute right-1 top-1/2 -translate-y-1/2">
          <EpisodeActionsMenu view={view} episode={id} actions={actions} label={name} />
        </span>
      </li>
    );
  };

  return (
    <nav aria-label="集目录" className="space-y-4 px-2 py-3 pb-24">
      {groups.map((group) => (
        <div key={group.file.source_file} className="space-y-0.5">
          {multiFile || groups.length > 0 ? (
            <button
              type="button"
              onClick={() => onScrollToFile(group.file.source_file)}
              className="focus-ring flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-left text-[11.5px] text-muted-foreground hover:text-foreground"
              title={group.file.name}
            >
              <FileText className="size-3.5 shrink-0" aria-hidden />
              <span className="truncate">{group.file.name}</span>
            </button>
          ) : null}
          <ul className="space-y-px">
            {group.rows.map((r) =>
              r.kind === "episode" ? (
                row(r.episode, r.info)
              ) : (
                <li key={r.key} className="flex items-center gap-2 rounded-md border border-dashed border-border px-2.5 py-1 text-[11.5px] text-muted-foreground">
                  <span className="flex-1">未分集 {formatVolume(t, r.units, view.unit)}</span>
                  <PlanGapButton sourceFile={r.sourceFile} end={r.end} blocked={actions.replanBlocked} />
                </li>
              ),
            )}
          </ul>
          {group.tailUnits > 0 ? (
            <p className="px-2.5 pt-1 text-[11.5px] text-muted-foreground">之后还有 {formatVolume(t, group.tailUnits, view.unit)} 未分集</p>
          ) : null}
        </div>
      ))}
      {others.length > 0 ? (
        <div className="space-y-0.5">
          <h3 className="px-2 py-1 text-[11.5px] text-muted-foreground">其他集（自带原文或无原文）</h3>
          <ul className="space-y-px">{others.map(({ episode, info }) => row(episode, info))}</ul>
        </div>
      ) : null}
    </nav>
  );
}

// ---------------------------------------------------------------------------
// 分集清单（tabs 结构的第二个 tab）：表格，宽窗口多出的宽度给开头与结尾两列
// ---------------------------------------------------------------------------

export function EpisodesTable({
  view,
  episodes,
  onLocate,
  actions,
}: {
  view: EpisodesView;
  episodes: EpisodeMeta[];
  onLocate: (episode: number) => void;
  actions: EpisodeMenuActions;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const info = new Map(view.episodes.map((episode) => [episode.episode, episode]));
  const ordered = [...episodes].sort(
    (a, b) => (episodePosition(episodes, a.episode) ?? 1e9) - (episodePosition(episodes, b.episode) ?? 1e9),
  );
  return (
    <div className="min-h-0 flex-1 overflow-auto px-4 pb-24">
      <Table className="table-fixed text-[12.5px]">
        <TableHeader className="sticky top-0 z-10 bg-background">
          <TableRow>
            <TableHead className="w-[72px]">集</TableHead>
            <TableHead className="w-[28%]">标题与钩子</TableHead>
            <TableHead className="w-[92px]">原文来源</TableHead>
            <TableHead className="w-[120px] text-right">体量</TableHead>
            <TableHead>开头</TableHead>
            <TableHead>结尾</TableHead>
            <TableHead className="w-10">
              <span className="sr-only">操作</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {ordered.map((episode) => {
            const id = episode.episode;
            const position = episodePosition(episodes, id);
            const name = position === null ? "未排期" : `第 ${position} 集`;
            const i = info.get(id);
            return (
              <TableRow key={id} className="group/row cursor-pointer" onClick={() => onLocate(id)}>
                <TableCell className="tabular-nums">
                  <span className="flex items-center gap-2">
                    <span aria-hidden className="h-3.5 w-[3px] shrink-0 rounded-full" style={{ background: episodeColor(id) }} />
                    {name}
                  </span>
                </TableCell>
                <TableCell className="whitespace-normal">
                  <span className="flex items-center gap-1.5">
                    <span className="truncate text-foreground">{episode.title?.trim() || "未命名"}</span>
                    {episode.ledger_status === "stale" ? <ReplannedBadge /> : null}
                  </span>
                  {episode.hook ? (
                    <span className="mt-0.5 line-clamp-1 text-[11.5px] text-muted-foreground" title={episode.hook}>
                      {episode.hook}
                    </span>
                  ) : null}
                </TableCell>
                <TableCell className="text-muted-foreground">{i ? t(`dashboard:episodes_view_origin_${i.origin}`) : "—"}</TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground">
                  {i?.units != null ? formatVolume(t, i.units, view.unit) : "—"}
                  {i?.spoken_seconds != null ? <span className="block text-[11px]">{formatSpoken(t, i.spoken_seconds)}</span> : null}
                </TableCell>
                <TableCell className="truncate text-muted-foreground" title={i?.first_sentence}>
                  {i?.first_sentence || "—"}
                </TableCell>
                <TableCell className="truncate text-muted-foreground" title={i?.last_sentence}>
                  {i?.last_sentence || "—"}
                </TableCell>
                <TableCell onClick={(event) => event.stopPropagation()}>
                  <EpisodeActionsMenu view={view} episode={id} actions={actions} label={name} />
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 模拟的重新规划候选：从第 4 集起把剩余原文重新切成更少的集。只在内存里，采纳与放弃会失败。
// ---------------------------------------------------------------------------

export function mockReplan(view: EpisodesView, episodes: EpisodeMeta[]): ReplanSummary | null {
  const file = view.files[0];
  if (!file) return null;
  const placed = file.segments.filter((s) => s.kind === "episode" && s.episode !== null && !s.continued);
  if (placed.length < 5) return null;
  const from = placed[3];
  const replaced = placed.slice(3);
  const text = file.segments.map((s) => s.text).join("");
  const chars = [...text];
  const start = from.start;
  const end = file.length;
  const count = Math.max(2, replaced.length - 2);
  const step = Math.floor((end - start) / count);
  const candidates: ReplanCandidateEpisode[] = [];
  for (let i = 0; i < count; i += 1) {
    const s = start + i * step;
    const e = i === count - 1 ? end : s + step;
    const body = chars.slice(s, e).join("").trim();
    const sentences = body.split(/(?<=[。！？])/).filter((x) => x.trim());
    candidates.push({
      title: ["重新规划 · 轮盘赌", "毕业礼", "恩人与仇人", "致命的犹豫", "疯狂施舍", "液化计划", "最终抉择", "两个物种"][i] ?? `新第 ${i + 4} 集`,
      hook: "（模拟）新方案的钩子。",
      source_file: file.source_file,
      start: s,
      end: e,
      units: e - s,
      first_sentence: sentences[0]?.trim().slice(0, 40) ?? "",
      last_sentence: sentences.at(-1)?.trim().slice(0, 40) ?? "",
      same_as: null,
      overlaps: replaced.filter((r) => r.start < e && s < r.end).map((r) => r.episode as number),
    });
  }
  void episodes;
  return {
    id: "prototype-mock",
    episode: from.episode as number,
    instructions: "节奏更紧凑，每集 3,000 字左右",
    complete: true,
    interrupted: null,
    stale: null,
    start: { source_file: file.source_file, offset: start },
    end: { source_file: file.source_file, offset: end },
    old_count: replaced.length,
    new_count: count,
    units: end - start,
    average_units: Math.round((end - start) / count),
    retired: [],
    removed: replaced.map((r) => r.episode as number),
    needs_review: [],
    uncovered: [],
    moved: [],
    episodes: candidates,
  };
}
