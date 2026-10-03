import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useSearch } from "wouter";
import { BookOpen, Upload } from "lucide-react";

import { API } from "@/api";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { useProjectsStore } from "@/stores/projects-store";
import type { EpisodesView as EpisodesViewData } from "@/types";
import { errMsg } from "@/utils/async";

import { EpisodesRail } from "./EpisodesRail";
import { ExternalChangeNotice } from "./ExternalChangeNotice";
import { SourceManuscript } from "./SourceManuscript";
import { SourceUploadDialog } from "./SourceUploadDialog";
import { ManualSplitToolbar, caretColor } from "./ManualSplitToolbar";
import { useManualSplit } from "./useManualSplit";
import { CreateEpisodeDialog } from "./CreateEpisodeDialog";
import { useDeleteEpisode } from "./useDeleteEpisode";
import { useReplanEpisode } from "./useReplanEpisode";
import { replanCompare } from "./replan-compare-model";
import { ReplanCandidatePanel } from "./ReplanCandidatePanel";
import { UnregisteredFilesPanel } from "./UnregisteredFilesPanel";
import { EPISODE_PLANNING_SLOTS } from "@/actions/generation";
import { useActiveResourceIds } from "@/stores/tasks-store";
import { useSourcesProto } from "@/prototype/sources/store";
import {
  EpisodeOutline,
  EpisodesHeaderBar,
  EpisodesTable,
  UnregisteredBanner,
  mockReplan,
  type EpisodeMenuActions,
} from "@/prototype/sources/EpisodesProto";
import {
  EPISODES_VIEW_CREATE_PARAM,
  EPISODES_VIEW_EPISODE_PARAM,
  EPISODES_VIEW_UPLOAD_PARAM,
  episodesViewPath,
  type SourceUploadMode,
} from "./episodes-view-model";

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function scrollIntoViewTop(el: HTMLElement | undefined) {
  el?.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "start" });
}

/** 解析地址上的打开请求；没有可识别的参数时返回 null。 */
function parseViewRequest(
  search: string,
): { upload: SourceUploadMode | null; episode: number | null; create: boolean } | null {
  const params = new URLSearchParams(search);
  const uploadParam = params.get(EPISODES_VIEW_UPLOAD_PARAM);
  const episodeParam = params.get(EPISODES_VIEW_EPISODE_PARAM);
  const create = params.get(EPISODES_VIEW_CREATE_PARAM) !== null;
  if (uploadParam === null && episodeParam === null && !create) return null;
  const episode = Number(episodeParam);
  return {
    upload: uploadParam === "whole_source" || uploadParam === "episode" ? uploadParam : null,
    episode: episodeParam !== null && Number.isInteger(episode) && episode > 0 ? episode : null,
    create,
  };
}

/**
 * 拉取「分集」视图数据：项目数据每次刷新（上传、处置文件、账本改动经 SSE 推送）后重拉一次；
 * 重拉期间保留上一份数据，不闪空。
 */
function useEpisodesViewData(projectName: string) {
  const project = useProjectsStore((s) => s.currentProjectData);
  const [data, setData] = useState<{ key: string; view: EpisodesViewData } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    API.getEpisodesView(projectName, { signal: controller.signal })
      .then((view) => {
        if (controller.signal.aborted) return;
        setData({ key: projectName, view });
        setError(null);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(errMsg(err));
      });
    return () => controller.abort();
  }, [projectName, project, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);
  return { view: data?.key === projectName ? data.view : null, error, reload, episodes: project?.episodes ?? [] };
}

/**
 * 项目层「分集」视图：左栏是整本源文全文、按集分段，右栏是上传、源文进度与集清单。
 *
 * 查询参数 `upload=whole_source|episode` 打开上传对话框，`episode=<集 ID>` 选中这一集并滚动到它，
 * `create` 打开新建一集对话框。
 */
export function EpisodesView({ projectName }: { projectName: string }) {
  const { t } = useTranslation("dashboard");
  const { view: rawView, error, reload, episodes } = useEpisodesViewData(projectName);
  // PROTOTYPE（#2982）：结构与入口位置由原型轴决定；「演示状态」可以在内存里造一份新的分集方案
  const { axes } = useSourcesProto();
  const view = useMemo(
    () =>
      rawView !== null && axes.mock === "replan" && rawView.replan === null
        ? { ...rawView, replan: mockReplan(rawView, episodes) }
        : rawView,
    [rawView, axes.mock, episodes],
  );
  const activePlanning = useActiveResourceIds("text_episode_plan", projectName);
  const planning = EPISODE_PLANNING_SLOTS.some((slot) => activePlanning.has(slot));
  const [current, setCurrent] = useState<number | null>(null);
  const [tab, setTab] = useState<"source" | "list">("source");
  const search = useSearch();
  const [, setLocation] = useLocation();
  const [selected, setSelected] = useState<number | null>(null);
  const [upload, setUpload] = useState<SourceUploadMode | null>(null);
  /** 新建一集对话框：undefined 为关闭，null 放在末尾，数字为插在这一集之后。 */
  const [createAfter, setCreateAfter] = useState<number | null | undefined>(undefined);
  const deletion = useDeleteEpisode(projectName, (episode) => {
    if (selected === episode) setSelected(null);
  });
  const episodeHeaders = useRef(new Map<number, HTMLElement>());
  const fileBars = useRef(new Map<string, HTMLElement>());
  // 开始重新规划时左栏滚到重新规划的起点：发起的那一集
  const onReplanStarted = useCallback((episode: number) => scrollIntoViewTop(episodeHeaders.current.get(episode)), []);
  const replan = useReplanEpisode(projectName, onReplanStarted);
  const compare = useMemo(() => (view === null ? null : replanCompare(view, view.replan)), [view]);

  const registerEpisodeHeader = useCallback((episode: number, el: HTMLElement | null) => {
    if (el) episodeHeaders.current.set(episode, el);
    else episodeHeaders.current.delete(episode);
  }, []);
  const registerFileBar = useCallback((sourceFile: string, el: HTMLElement | null) => {
    if (el) fileBars.current.set(sourceFile, el);
    else fileBars.current.delete(sourceFile);
  }, []);

  const selectFromRail = useCallback((episode: number) => {
    setSelected(episode);
    scrollIntoViewTop(episodeHeaders.current.get(episode));
  }, []);
  const scrollToFile = useCallback((sourceFile: string) => scrollIntoViewTop(fileBars.current.get(sourceFile)), []);

  // 查询参数只消费一次：渲染时读出并记下已消费的地址，effect 再把参数从地址里去掉，返回或刷新不会再次打开对话框
  const [consumedSearch, setConsumedSearch] = useState<string | null>(null);
  const [scrollTarget, setScrollTarget] = useState<{ episode: number } | null>(null);
  const request = parseViewRequest(search);
  if (request !== null && search !== consumedSearch) {
    setConsumedSearch(search);
    if (request.upload !== null) setUpload(request.upload);
    if (request.create) setCreateAfter(null);
    if (request.episode !== null) {
      setSelected(request.episode);
      setScrollTarget({ episode: request.episode });
    }
  } else if (request === null && consumedSearch !== null) {
    // 参数已从地址去掉：清掉记录，同一地址再次到达时照常生效
    setConsumedSearch(null);
  }

  useEffect(() => {
    if (parseViewRequest(search) !== null) setLocation(episodesViewPath(), { replace: true });
  }, [search, setLocation]);

  const loaded = view !== null;
  useEffect(() => {
    if (loaded && scrollTarget !== null) scrollIntoViewTop(episodeHeaders.current.get(scrollTarget.episode));
  }, [loaded, scrollTarget]);

  const openUpload = useCallback(() => setUpload("whole_source"), []);

  const onSplitApplied = useCallback(
    (episode: number | null) => {
      reload();
      if (episode !== null) setSelected(episode);
    },
    [reload],
  );
  const split = useManualSplit(projectName, view, onSplitApplied);
  const caret =
    view !== null && split.pending !== null && split.action !== null
      ? {
          point: split.pending,
          color: caretColor(split.action),
          toolbar: (
            <ManualSplitToolbar
              view={view}
              episodes={episodes}
              action={split.action}
              title={split.title}
              onTitleChange={split.setTitle}
              busy={split.busy}
              onConfirm={split.confirmPending}
              onCancel={split.cancel}
            />
          ),
        }
      : null;

  if (view === null) {
    return (
      <div className="grid h-full place-items-center px-6 text-center text-[12.5px] text-text-4" aria-busy={!error}>
        {error ? t("episodes_view_load_failed", { message: error }) : t("episodes_view_loading")}
      </div>
    );
  }

  // 原文滚动时，目录高亮视口顶部所在的那一集
  const onManuscriptScroll = (event: React.UIEvent<HTMLElement>) => {
    const top = event.currentTarget.getBoundingClientRect().top + 96;
    let found: number | null = null;
    let best = -Infinity;
    for (const [episode, el] of episodeHeaders.current) {
      const y = el.getBoundingClientRect().top;
      if (y <= top && y > best) {
        best = y;
        found = episode;
      }
    }
    if (found !== current) setCurrent(found);
  };
  const locate = (episode: number) => {
    setSelected(episode);
    setCurrent(episode);
    if (tab !== "source") {
      setTab("source");
      requestAnimationFrame(() => requestAnimationFrame(() => scrollIntoViewTop(episodeHeaders.current.get(episode))));
    } else scrollIntoViewTop(episodeHeaders.current.get(episode));
  };
  const menuActions: EpisodeMenuActions = {
    busy: split.busy,
    replanBlocked: view.replan !== null ? t("replan_pending_hint") : planning ? t("episode_planning_busy") : null,
    onReplan: (episode) => void replan.requestReplan(episode),
    onMergeWithNext: split.mergeWithNext,
    onClearAfter: split.clearAfter,
    onCreate: setCreateAfter,
    onDelete: (episode) => void deletion.requestDelete(episode),
  };

  const layout = axes.layout;
  // 目录与上下 tab 两种结构没有右栏可放工具，入口固定在页头
  const toolsInHeader = layout !== "split" || axes.tools === "header";
  const banner = axes.notices === "banner";

  const manuscript = (
    <main
      className="min-h-0 flex-1 overflow-y-auto px-6 @min-[900px]/epv:px-10"
      aria-label={t("episodes_view_source_label")}
      onScroll={onManuscriptScroll}
    >
      <div className="mx-auto max-w-[40em]">
        <ExternalChangeNotice projectName={projectName} changes={view.external_changes} onLocate={scrollToFile} />
        {view.files.length === 0 ? (
          <EmptySource hasEpisodes={episodes.length > 0} onUpload={openUpload} />
        ) : (
          <SourceManuscript
            projectName={projectName}
            view={view}
            episodes={episodes}
            selected={selected}
            onSelect={setSelected}
            registerEpisodeHeader={registerEpisodeHeader}
            registerFileBar={registerFileBar}
            caret={caret}
            moving={split.moving}
            onPlace={split.place}
            onToggleMoving={split.toggleMoving}
            compare={compare}
          />
        )}
      </div>
    </main>
  );

  const replanAside =
    view.replan !== null ? (
      <aside
        className="w-[clamp(320px,30cqw,400px)] min-h-0 shrink-0 space-y-4 overflow-y-auto border-l border-border bg-sidebar/40 px-4 py-4"
        aria-label="新的分集方案"
      >
        <ReplanCandidatePanel
          projectName={projectName}
          view={view}
          replan={view.replan}
          episodes={episodes}
          generating={planning}
          onChanged={reload}
        />
      </aside>
    ) : null;

  let body: React.ReactNode;
  if (layout === "outline") {
    const outlineYields = view.replan !== null && axes.replanLayout === "yield";
    body = (
      <>
        {outlineYields ? null : (
        <aside className="w-[clamp(240px,26cqw,300px)] min-h-0 shrink-0 overflow-y-auto border-r border-border" aria-label="集目录">
          {!banner && view.unregistered.length > 0 ? (
            <div className="px-3 pt-3">
              <UnregisteredFilesPanel projectName={projectName} files={view.unregistered} episodes={episodes} onChanged={reload} />
            </div>
          ) : null}
          <EpisodeOutline
            projectName={projectName}
            view={view}
            episodes={episodes}
            current={current ?? selected}
            onLocate={locate}
            onScrollToFile={scrollToFile}
            actions={menuActions}
          />
        </aside>
        )}
        {manuscript}
        {replanAside}
      </>
    );
  } else if (layout === "tabs") {
    body =
      tab === "list" ? (
        <div className="flex min-h-0 flex-1 flex-col pt-2">
          <EpisodesTable view={view} episodes={episodes} onLocate={locate} actions={menuActions} />
        </div>
      ) : (
        <>
          {manuscript}
          {replanAside}
        </>
      );
  } else {
    body = (
      <>
        {manuscript}
        <aside
          className="w-[clamp(300px,28cqw,380px)] min-h-0 shrink-0 overflow-y-auto border-l border-border"
          style={{ background: "oklch(0.18 0.01 265 / 0.5)" }}
          aria-label={t("episodes_view_rail_label")}
        >
          <EpisodesRail
            projectName={projectName}
            view={view}
            episodes={episodes}
            selected={selected}
            onSelect={selectFromRail}
            onScrollToFile={scrollToFile}
            onUpload={openUpload}
            onChanged={reload}
            splitBusy={split.busy}
            onMergeWithNext={split.mergeWithNext}
            onClearAfter={split.clearAfter}
            onCreate={setCreateAfter}
            onDelete={(episode) => void deletion.requestDelete(episode)}
            onReplan={(episode) => void replan.requestReplan(episode)}
            compact={axes.list === "compact"}
            toolsInHeader={toolsInHeader}
            hideUnregistered={banner}
          />
        </aside>
      </>
    );
  }

  return (
    <div className="@container/epv flex h-full flex-col">
      {toolsInHeader ? (
        <EpisodesHeaderBar
          projectName={projectName}
          view={view}
          episodes={episodes}
          onUpload={setUpload}
          onCreate={() => setCreateAfter(null)}
        >
          {layout === "tabs" ? (
            <div role="tablist" aria-label="分集视图" className="ml-2 flex shrink-0 items-center gap-0.5 rounded-lg bg-muted/60 p-0.5">
              {(
                [
                  ["source", "原文"],
                  ["list", "分集清单"],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  role="tab"
                  aria-selected={tab === key}
                  onClick={() => setTab(key)}
                  className={`focus-ring rounded-md px-3 py-1 text-[12.5px] font-medium transition-colors ${
                    tab === key ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          ) : null}
        </EpisodesHeaderBar>
      ) : null}
      {banner ? <UnregisteredBanner projectName={projectName} view={view} episodes={episodes} onChanged={reload} /> : null}
      <div className="flex min-h-0 flex-1">{body}</div>
      {upload !== null ? (
        <SourceUploadDialog projectName={projectName} initialMode={upload} onClose={() => setUpload(null)} />
      ) : null}
      {createAfter !== undefined ? (
        <CreateEpisodeDialog
          projectName={projectName}
          initialAfter={createAfter}
          onClose={() => setCreateAfter(undefined)}
          onCreated={(episode) => {
            setCreateAfter(undefined);
            setSelected(episode);
            reload();
          }}
        />
      ) : null}
      {deletion.dialog}
      {replan.dialog}
      {split.dialog}
    </div>
  );
}

function EmptySource({ hasEpisodes, onUpload }: { hasEpisodes: boolean; onUpload: () => void }) {
  const { t } = useTranslation("dashboard");
  return (
    <div className="mx-auto mt-24 max-w-md text-center">
      <BookOpen className="mx-auto h-6 w-6 text-text-4" aria-hidden />
      <h2 className="display-serif mt-4 text-[17px] font-semibold tracking-tight text-text">
        {t("episodes_view_empty_title")}
      </h2>
      <p className="mt-2 text-[12.5px] leading-[1.7] text-text-3">
        {hasEpisodes ? t("episodes_view_empty_has_episodes") : t("episodes_view_empty_hint")}
      </p>
      <PrimaryButton className="mt-5" onClick={onUpload} leadingIcon={<Upload className="h-4 w-4" aria-hidden />}>
        {t("source_upload_title")}
      </PrimaryButton>
    </div>
  );
}
