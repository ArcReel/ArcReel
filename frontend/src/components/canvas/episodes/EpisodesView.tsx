import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useSearch } from "wouter";
import { BookOpen, Upload } from "lucide-react";

import { API } from "@/api";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { useProjectsStore } from "@/stores/projects-store";
import type { EpisodesView as EpisodesViewData } from "@/types";
import { errMsg } from "@/utils/async";

import { EpisodesRail } from "./EpisodesRail";
import { SourceManuscript } from "./SourceManuscript";
import { SourceUploadDialog } from "./SourceUploadDialog";
import { ManualSplitToolbar, caretColor } from "./ManualSplitToolbar";
import { useManualSplit } from "./useManualSplit";
import { CreateEpisodeDialog } from "./CreateEpisodeDialog";
import { useDeleteEpisode } from "./useDeleteEpisode";
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
  const { view, error, reload, episodes } = useEpisodesViewData(projectName);
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

  return (
    <div className="flex h-full flex-col lg:flex-row">
      <main className="min-h-0 flex-1 overflow-y-auto px-6 lg:px-10" aria-label={t("episodes_view_source_label")}>
        <div className="mx-auto max-w-[44em]">
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
            />
          )}
        </div>
      </main>
      <aside
        className="min-h-0 shrink-0 overflow-y-auto border-hairline max-lg:max-h-[45%] max-lg:border-t lg:w-[360px] lg:border-l"
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
        />
      </aside>
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
