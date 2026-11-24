import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useSearch } from "wouter";
import { BookOpen, Upload } from "lucide-react";

import { API } from "@/api";
import { EPISODE_PLANNING_SLOTS } from "@/actions/generation";
import { Button } from "@/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { useProjectsStore } from "@/stores/projects-store";
import { useActiveResourceIds } from "@/stores/tasks-store";
import type { EpisodesView as EpisodesViewData } from "@/types";
import { errMsg } from "@/utils/async";

import { CreateEpisodeDialog } from "./CreateEpisodeDialog";
import { EpisodeOutline, type EpisodeMenuActions } from "./EpisodeOutline";
import { EpisodesHeader } from "./EpisodesHeader";
import { ExternalChangeNotice } from "./ExternalChangeNotice";
import { ManualSplitToolbar } from "./ManualSplitToolbar";
import { ReplanCandidatePanel } from "./ReplanCandidatePanel";
import { SourceManuscript } from "./SourceManuscript";
import { SourceUploadDialog } from "./SourceUploadDialog";
import { UnregisteredFilesBanner } from "./UnregisteredFiles";
import { caretEpisode } from "./manual-split-model";
import { replanCompare } from "./replan-compare-model";
import { useDeleteEpisode } from "./useDeleteEpisode";
import { useManualSplit } from "./useManualSplit";
import { useReplanEpisode } from "./useReplanEpisode";
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

function useCurrentEpisode(scrollRoot: RefObject<HTMLElement | null>, view: EpisodesViewData | null) {
  const [current, setCurrent] = useState<number | null>(null);
  useEffect(() => {
    const root = scrollRoot.current;
    if (root === null || view === null || typeof IntersectionObserver === "undefined") return;
    const visible = new Set<HTMLElement>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const target = entry.target as HTMLElement;
          if (entry.isIntersecting) visible.add(target);
          else visible.delete(target);
        }
        let top: { y: number; episode: number } | null = null;
        for (const target of visible) {
          const y = target.getBoundingClientRect().top;
          if (top === null || y < top.y) top = { y, episode: Number(target.dataset.episodeBlock) };
        }
        if (top !== null) setCurrent(top.episode);
      },
      { root, rootMargin: "0px 0px -75% 0px" },
    );
    for (const block of root.querySelectorAll<HTMLElement>("[data-episode-block]")) observer.observe(block);
    return () => observer.disconnect();
  }, [scrollRoot, view]);
  return [current, setCurrent] as const;
}

export function EpisodesView({ projectName }: { projectName: string }) {
  const { t } = useTranslation("dashboard");
  const { view, error, reload, episodes } = useEpisodesViewData(projectName);
  const search = useSearch();
  const [, setLocation] = useLocation();
  const [upload, setUpload] = useState<SourceUploadMode | null>(null);
  const [createAfter, setCreateAfter] = useState<number | null | undefined>(undefined);
  const deletion = useDeleteEpisode(projectName);
  const manuscriptRef = useRef<HTMLElement>(null);
  const [current, setCurrent] = useCurrentEpisode(manuscriptRef, view);
  const episodeHeaders = useRef(new Map<number, HTMLElement>());
  const fileBars = useRef(new Map<string, HTMLElement>());
  const activePlanning = useActiveResourceIds("text_episode_plan", projectName);
  const planning = EPISODE_PLANNING_SLOTS.some((slot) => activePlanning.has(slot));

  const locate = useCallback(
    (episode: number) => {
      setCurrent(episode);
      scrollIntoViewTop(episodeHeaders.current.get(episode));
    },
    [setCurrent],
  );
  const locateFile = useCallback((sourceFile: string) => scrollIntoViewTop(fileBars.current.get(sourceFile)), []);
  const replan = useReplanEpisode(projectName, locate);
  const compare = useMemo(() => (view === null ? null : replanCompare(view, view.replan)), [view]);

  const registerEpisodeHeader = useCallback((episode: number, el: HTMLElement | null) => {
    if (el) episodeHeaders.current.set(episode, el);
    else episodeHeaders.current.delete(episode);
  }, []);
  const registerFileBar = useCallback((sourceFile: string, el: HTMLElement | null) => {
    if (el) fileBars.current.set(sourceFile, el);
    else fileBars.current.delete(sourceFile);
  }, []);

  const [scrollTarget, setScrollTarget] = useState<{ episode: number } | null>(null);
  const scrolledTarget = useRef<{ episode: number } | null>(null);
  useEffect(() => {
    if (scrollTarget === null || scrolledTarget.current === scrollTarget) return;
    const header = episodeHeaders.current.get(scrollTarget.episode);
    if (header === undefined) return;
    scrolledTarget.current = scrollTarget;
    // react-doctor-disable-next-line react-doctor/no-adjust-state-on-prop-change -- 要等原文渲染出这一集的标题节点才能滚动，当前集随这次滚动一起更新，与 locate 同一做法
    setCurrent(scrollTarget.episode);
    scrollIntoViewTop(header);
  }, [view, scrollTarget, setCurrent]);

  const [consumedSearch, setConsumedSearch] = useState<string | null>(null);
  const request = parseViewRequest(search);
  if (request !== null && search !== consumedSearch) {
    setConsumedSearch(search);
    if (request.upload !== null) setUpload(request.upload);
    if (request.create) setCreateAfter(null);
    if (request.episode !== null) setScrollTarget({ episode: request.episode });
  } else if (request === null && consumedSearch !== null) {
    setConsumedSearch(null);
  }

  useEffect(() => {
    if (parseViewRequest(search) !== null) setLocation(episodesViewPath(), { replace: true });
  }, [search, setLocation]);

  const onSplitApplied = useCallback(
    (episode: number | null) => {
      reload();
      if (episode !== null) setScrollTarget({ episode });
    },
    [reload],
  );
  const split = useManualSplit(projectName, view, onSplitApplied);
  const caret =
    view !== null && split.pending !== null && split.action !== null
      ? {
          point: split.pending,
          episode: caretEpisode(split.action),
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
      <div
        className="flex min-h-0 flex-1 items-center justify-center px-6 text-center text-sm text-muted-foreground"
        aria-busy={!error}
      >
        {error ? t("episodes_view_load_failed", { message: error }) : t("episodes_view_loading")}
      </div>
    );
  }

  const menuActions: EpisodeMenuActions = {
    splitBusy: split.busy,
    planBlocked:
      view.replan !== null ? t("replan_pending_hint") : planning ? t("episode_planning_busy") : null,
    onCreateAfter: setCreateAfter,
    onMergeWithNext: split.mergeWithNext,
    onClearAfter: split.clearAfter,
    onReplan: (episode) => void replan.requestReplan(episode),
    onDelete: (episode) => void deletion.requestDelete(episode),
  };
  const replanPending = view.replan !== null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <EpisodesHeader
        projectName={projectName}
        view={view}
        episodeCount={episodes.length}
        planning={planning}
        onUpload={setUpload}
        onCreate={() => setCreateAfter(null)}
      />
      <UnregisteredFilesBanner
        projectName={projectName}
        files={view.unregistered}
        episodes={episodes}
        onChanged={reload}
      />
      <div className="flex min-h-0 flex-1">
        {!replanPending && episodes.length > 0 ? (
          <EpisodeOutline
            view={view}
            episodes={episodes}
            current={current}
            onLocate={locate}
            onLocateFile={locateFile}
            actions={menuActions}
          />
        ) : null}
        <section
          ref={manuscriptRef}
          // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- 原文是长文本只读区，须能用键盘聚焦后以方向键与翻页键滚动
          tabIndex={0}
          aria-label={t("episodes_view_source_label")}
          className="focus-ring relative min-h-0 min-w-0 flex-1 overflow-y-auto px-6 [scrollbar-gutter:stable]"
        >
          <div className="mx-auto max-w-[40em] pt-4">
            <ExternalChangeNotice projectName={projectName} changes={view.external_changes} onLocate={locateFile} />
            {view.files.length === 0 ? (
              <EmptySource hasEpisodes={episodes.length > 0} onUpload={() => setUpload("whole_source")} />
            ) : (
              <SourceManuscript
                projectName={projectName}
                view={view}
                episodes={episodes}
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
        </section>
        {view.replan !== null ? (
          <section
            aria-label={t("episodes_plan_column_label")}
            className="relative w-[clamp(320px,30cqw,400px)] shrink-0 overflow-y-auto border-l px-4 py-4"
          >
            <ReplanCandidatePanel
              projectName={projectName}
              view={view}
              replan={view.replan}
              episodes={episodes}
              generating={planning}
              onChanged={reload}
            />
          </section>
        ) : null}
      </div>
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
            if (episode !== null) setScrollTarget({ episode });
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
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <BookOpen aria-hidden />
        </EmptyMedia>
        <EmptyTitle>{t("episodes_view_empty_title")}</EmptyTitle>
        <EmptyDescription>
          {hasEpisodes ? t("episodes_view_empty_has_episodes") : t("episodes_view_empty_hint")}
        </EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        <Button onClick={onUpload}>
          <Upload aria-hidden data-icon="inline-start" />
          {t("source_upload_title")}
        </Button>
      </EmptyContent>
    </Empty>
  );
}
