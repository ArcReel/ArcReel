import { useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useParams, useSearchParams } from "wouter";

import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type {
  EditPreviewNarrationAudio,
  EditTimelineIssue,
  EditTimelinePreviewMedia,
  EditTimelineReadout,
  EditTimelineSummary,
} from "@/types/edit-timeline";
import { LINK_TIME_PARAM, LINK_TIMELINE_PARAM, parseSeconds } from "@/utils/app-link";
import { errMsg } from "@/utils/async";
import { formatRelativeTime, isJustNow } from "@/utils/date-format";
import type { PreviewAspect } from "@/utils/preview-aspect";

import { ClipInspector, ISSUE_LIST_HEADING_ID, IssueList } from "./EditTimelineDetails";
import { EditTimelineMenu } from "./EditTimelineMenu";
import { EditTimelinePlayer } from "./EditTimelinePlayer";
import { EditTimelineTracks } from "./EditTimelineTracks";
import { buildPlaybackPlan, locate, type PlaybackPlan, type PlaybackSegment } from "./playback-schedule";
import {
  bgmPlacements,
  narrationPlacements,
  narrationSpans,
  placeSubtitles,
  type AudioPlacement,
} from "./preview-tracks";
import {
  isReferenceVideoScript,
  issueClipIds,
  pickDefaultTimeline,
  unitThumbnails,
  unitVideoPath,
  unusedUnitIds,
} from "./timeline-view";
import { useTimelinePlayback } from "./useTimelinePlayback";

/** 标签行右侧操作区拿到的当前剪辑时间线。 */
export interface EditTimelineActionsContext {
  timelineId: string;
  timelineName: string;
  /** 当前读取结果的 issues；读取完成前为 null。 */
  issues: readonly EditTimelineIssue[] | null;
  /** 滚动到「问题」列表并把焦点移过去。 */
  showIssues: () => void;
}

export interface EditTimelineEmptyStateContext {
  /** 新建剪辑时间线后调用，重新读取列表并选中最近修改的那条。 */
  reload: () => void;
}

interface EditTimelineViewProps {
  projectName: string;
  episode: number;
  /** 本集脚本：用于视频单元的源视频路径与缩略图。 */
  script: unknown;
  aspect: PreviewAspect;
  /** 项目的旁白交付方式是否为 TTS 配音；否则为后期配音，旁白轨不按缺配音处理。 */
  ttsNarration: boolean;
  /** 标签行右侧的操作区；不传时不渲染。 */
  renderActions?: (context: EditTimelineActionsContext) => ReactNode;
  /** 本集没有剪辑时间线时的空状态。 */
  renderEmptyState: (context: EditTimelineEmptyStateContext) => ReactNode;
}

/** 应用内链接带来的一次性定位：打开哪条剪辑时间线（缺省为当前选中的）、定位到全局时间的哪一秒（缺省为不动播放头）。 */
interface LinkJump {
  timelineId: string | null;
  seconds: number | null;
}

type Loaded<T> = { key: string; value: T } | { key: string; error: string };

/**
 * 集页的剪辑视图：只读预览一条剪辑时间线，标签菜单可重命名与删除。上方播放器按剪辑时间线实时拼接播放，下方横向时间线；
 * 项目有变更（含 Agent 写入新修订）时重新读取。
 */
export function EditTimelineView({
  projectName,
  episode,
  script,
  aspect,
  ttsNarration,
  renderActions,
  renderEmptyState,
}: EditTimelineViewProps) {
  const { t, i18n } = useTranslation("dashboard");
  const snapshotRevision = useProjectsStore((s) => s.projectSnapshotRevisions[projectName] ?? 0);
  const [retry, setRetry] = useState(0);
  const listKey = `${projectName}::${episode}::${snapshotRevision}::${retry}`;
  const [list, setList] = useState<Loaded<EditTimelineSummary[]> | null>(null);
  const [chosenId, setChosenId] = useState<string | null>(null);
  const reload = useCallback(() => setRetry((n) => n + 1), []);
  const tabIdPrefix = useId();
  const tabs = useRef(new Map<string, HTMLButtonElement>());

  // 链接参数 tl / t 读入后立刻从地址栏去掉：它们是一次性的定位指令，刷新页面不应再次跳走。
  const [searchParams, setSearchParams] = useSearchParams();
  // 跳到别的项目时，地址先变、当前项目后切：这一刻地址栏里的参数属于即将挂载的那个视图，不在这里消费。
  const routeProject = useParams<{ projectName?: string }>().projectName;
  const linkForOtherProject = routeProject !== undefined && routeProject !== projectName;
  const linkTimeline = searchParams.get(LINK_TIMELINE_PARAM);
  const linkTime = searchParams.get(LINK_TIME_PARAM);
  const [jump, setJump] = useState<LinkJump | null>(null);
  useEffect(() => {
    if (linkForOtherProject || (linkTimeline === null && linkTime === null)) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 订阅地址栏的一次性参数：读入后清掉，再点同一条链接才能重新触发
    setJump({ timelineId: linkTimeline || null, seconds: parseSeconds(linkTime) });
    setSearchParams(
      (params) => {
        params.delete(LINK_TIMELINE_PARAM);
        params.delete(LINK_TIME_PARAM);
        return params;
      },
      { replace: true },
    );
  }, [linkForOtherProject, linkTimeline, linkTime, setSearchParams]);
  // 删除后选中的标签回落到默认那条（最近修改的）。
  const handleDeleted = useCallback(() => {
    setChosenId(null);
    setRetry((n) => n + 1);
  }, []);
  const showIssues = useCallback(() => {
    const heading = document.getElementById(ISSUE_LIST_HEADING_ID);
    heading?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    heading?.focus({ preventScroll: true });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    API.listEditTimelines(projectName, episode, { signal: controller.signal })
      .then(({ timelines }) => {
        if (controller.signal.aborted) return;
        setList({ key: listKey, value: timelines });
        // 首次看到剪辑时间线时固定默认选中的那条，之后新建或修改其他时间线不会把正在看的标签切走。
        setChosenId((previous) => previous ?? pickDefaultTimeline(timelines));
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) setList({ key: listKey, error: errMsg(cause) });
      });
    return () => controller.abort();
  }, [episode, listKey, projectName]);

  // 刷新期间沿用上一次的列表，避免每次项目变更都闪回加载态。
  const timelines = list && "value" in list ? list.value : null;

  // 列表到了才能判断链接指向的剪辑时间线还在不在：在就切过去，不在就提示并放弃这次定位。
  const jumpTimelineId = jump?.timelineId ?? null;
  const jumpTimelineKnown = timelines !== null && jumpTimelineId !== null && timelines.some((item) => item.id === jumpTimelineId);
  // 沿用中的旧列表可能还没有刚新建的那条，只按本轮读到的列表判定「不存在」。
  const listFresh = list?.key === listKey;
  useEffect(() => {
    if (!jump || !timelines) return;
    if (timelines.length === 0) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- 没有剪辑时间线可定位，作废这次定位
      setJump(null);
      return;
    }
    if (jumpTimelineId === null) return;
    if (jumpTimelineKnown) {
      setChosenId(jumpTimelineId);
    } else if (listFresh) {
      useAppStore.getState().pushToast(t("edit_view_link_timeline_missing"), "warning");
      setJump(null);
    }
  }, [jump, jumpTimelineId, jumpTimelineKnown, listFresh, timelines, t]);
  const handleJumpApplied = useCallback(() => setJump(null), []);
  const selectedId =
    timelines && chosenId && timelines.some((item) => item.id === chosenId)
      ? chosenId
      : timelines
        ? pickDefaultTimeline(timelines)
        : null;
  const selected = timelines?.find((item) => item.id === selectedId);

  // 每次项目变更都重新读取：读取结果还取决于视频单元的 current 版本，不只取决于修订。
  const readoutKey = `${listKey}::${selectedId ?? ""}`;
  const [readout, setReadout] = useState<(Loaded<EditTimelineReadout> & { timelineId: string }) | null>(null);
  useEffect(() => {
    if (!selectedId) return;
    const controller = new AbortController();
    API.getEditTimeline(projectName, selectedId, { signal: controller.signal })
      .then((value) => {
        if (!controller.signal.aborted) setReadout({ key: readoutKey, timelineId: selectedId, value });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        // 刷新失败时保留这条剪辑时间线上一次的读取结果，播放不被打断。
        setReadout((previous) =>
          previous?.timelineId === selectedId && "value" in previous
            ? previous
            : { key: readoutKey, timelineId: selectedId, error: errMsg(cause) },
        );
      });
    return () => controller.abort();
  }, [projectName, readoutKey, selectedId]);

  // 旁白配音与字幕随读取结果一起刷新；读取失败时沿用上一次的结果，还没有结果时只是不出旁白、不显示字幕。
  const [previewMedia, setPreviewMedia] = useState<{ timelineId: string; value: EditTimelinePreviewMedia } | null>(
    null,
  );
  useEffect(() => {
    if (!selectedId) return;
    const controller = new AbortController();
    API.getEditTimelinePreviewMedia(projectName, selectedId, { signal: controller.signal })
      .then((value) => {
        if (!controller.signal.aborted) setPreviewMedia({ timelineId: selectedId, value });
      })
      .catch(() => {});
    return () => controller.abort();
  }, [projectName, readoutKey, selectedId]);

  if (list && "error" in list && !timelines) {
    return <LoadFailed message={list.error} onRetry={reload} />;
  }
  if (!timelines) return <Centered>{t("edit_view_loading")}</Centered>;
  if (timelines.length === 0 || !selected) {
    return renderEmptyState({ reload });
  }

  const current = readout?.timelineId === selected.id ? readout : null;
  const tabId = (timelineId: string) => `${tabIdPrefix}-tab-${timelineId}`;
  const panelId = `${tabIdPrefix}-panel`;
  const choose = (timelineId: string) => {
    setChosenId(timelineId);
    // 手动切换标签后放弃尚未完成的链接定位。
    setJump(null);
  };
  // tablist 的键盘约定：Tab 进出控件组，方向键在组内切换。
  const onTabKeyDown = (event: KeyboardEvent) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const step = event.key === "ArrowRight" ? 1 : -1;
    const index = timelines.findIndex((item) => item.id === selected.id);
    const next = timelines[(index + step + timelines.length) % timelines.length];
    choose(next.id);
    tabs.current.get(next.id)?.focus();
  };
  const authorName = t(`edit_view_author_${selected.updated_by.kind}`);
  const updatedJustNow = isJustNow(selected.updated_at);
  const updatedAt = formatRelativeTime(selected.updated_at, i18n.language) ?? selected.updated_at;

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto px-6 py-5">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center rounded-[9px] border border-hairline bg-bg-grad-a/55 p-0.5">
          <div role="tablist" aria-label={t("edit_view_timelines_aria")} className="flex flex-wrap">
            {timelines.map((item) => (
              <button
                key={item.id}
                ref={(node) => {
                  if (node) tabs.current.set(item.id, node);
                  else tabs.current.delete(item.id);
                }}
                type="button"
                role="tab"
                id={tabId(item.id)}
                aria-selected={item.id === selected.id}
                aria-controls={panelId}
                tabIndex={item.id === selected.id ? 0 : -1}
                onClick={() => choose(item.id)}
                onKeyDown={onTabKeyDown}
                className={`focus-ring rounded-[7px] px-3 py-1.5 text-[12.5px] transition-colors ${
                  item.id === selected.id ? "bg-accent-dim text-text" : "text-text-3 hover:text-text"
                }`}
              >
                {item.name}
              </button>
            ))}
          </div>
          <EditTimelineMenu
            projectName={projectName}
            timeline={selected}
            onRenamed={reload}
            onDeleted={handleDeleted}
          />
        </div>
        <span className="text-[12px] text-text-4">
          {updatedJustNow
            ? t("edit_view_updated_just_now", { author: authorName })
            : t("edit_view_updated", { author: authorName, time: updatedAt })}
        </span>
        {renderActions && (
          <div className="ml-auto flex items-center gap-2">
            {renderActions({
              timelineId: selected.id,
              timelineName: selected.name,
              issues: current && "value" in current ? current.value.issues : null,
              showIssues,
            })}
          </div>
        )}
      </div>

      <div
        role="tabpanel"
        id={panelId}
        aria-labelledby={tabId(selected.id)}
        className="flex flex-1 flex-col gap-4"
      >
        {current && "value" in current ? (
          <TimelinePreview
            key={selected.id}
            projectName={projectName}
            readout={current.value}
            media={previewMedia?.timelineId === selected.id ? previewMedia.value : null}
            script={script}
            aspect={aspect}
            ttsNarration={ttsNarration}
            seekRequest={jump && (jump.timelineId === null || jump.timelineId === selected.id) ? jump : null}
            onSeekHandled={handleJumpApplied}
          />
        ) : current && "error" in current ? (
          <LoadFailed message={current.error} onRetry={reload} />
        ) : (
          <Centered>{t("edit_view_loading")}</Centered>
        )}
      </div>
    </div>
  );
}

interface TimelinePreviewProps {
  projectName: string;
  ttsNarration: boolean;
  readout: EditTimelineReadout;
  media: EditTimelinePreviewMedia | null;
  script: unknown;
  aspect: PreviewAspect;
  /** 链接要求定位的位置；处理完调用 `onSeekHandled`。 */
  seekRequest: LinkJump | null;
  onSeekHandled: () => void;
}

function TimelinePreview({
  projectName,
  ttsNarration,
  readout,
  media,
  script,
  aspect,
  seekRequest,
  onSeekHandled,
}: TimelinePreviewProps) {
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const [showSubtitles, setShowSubtitles] = useState(true);

  // 内容相同的重新读取不换计划引用，播放不因项目的无关变更而重新定位。
  const planJson = useMemo(() => JSON.stringify(buildPlaybackPlan(readout)), [readout]);
  const plan = useMemo(() => JSON.parse(planJson) as PlaybackPlan, [planJson]);
  // 源视频地址只随视频单元的 current 版本变化；脚本对象每次刷新都换引用，这里只取它的形状。
  const referenceVideo = isReferenceVideoScript(script);
  const sourceUrl = useCallback(
    (segment: PlaybackSegment) =>
      segment.hasVideo && segment.videoVersion !== null
        ? API.getFileUrl(projectName, unitVideoPath(referenceVideo, segment.unitId), segment.videoVersion)
        : null,
    [projectName, referenceVideo],
  );
  // 音频摆放与旁白配音地址同样按内容去重，内容不变的刷新不打断正在播放的音频。
  const audioJson = useMemo(
    () => JSON.stringify([...narrationPlacements(readout, media), ...bgmPlacements(readout, media)]),
    [readout, media],
  );
  const audio = useMemo(() => JSON.parse(audioJson) as AudioPlacement[], [audioJson]);
  const audioSourcesJson = useMemo(
    () =>
      JSON.stringify({
        narration: Object.fromEntries(
          (media?.units ?? []).flatMap((unit) => (unit.narration_audio ? [[unit.unit_id, unit.narration_audio]] : [])),
        ),
        bgm: Object.fromEntries((media?.bgm ?? []).map((item) => [item.bgm_id, item.path])),
      }),
    [media],
  );
  const audioUrl = useMemo(() => {
    const sources = JSON.parse(audioSourcesJson) as {
      narration: Record<string, EditPreviewNarrationAudio>;
      bgm: Record<string, string>;
    };
    return (placement: AudioPlacement) => {
      // BGM 按字节登记、文件不再改写，地址不带版本号。
      if (placement.kind === "bgm") {
        const path = sources.bgm[placement.sourceId];
        return path ? API.getFileUrl(projectName, path) : null;
      }
      const source = sources.narration[placement.sourceId];
      return source ? API.getFileUrl(projectName, source.path, source.version) : null;
    };
  }, [audioSourcesJson, projectName]);
  const playback = useTimelinePlayback(plan, sourceUrl, audio, audioUrl);
  const narration = useMemo(() => narrationSpans(readout, ttsNarration), [readout, ttsNarration]);
  const subtitles = useMemo(() => placeSubtitles(readout, plan, media), [readout, plan, media]);
  const bgm = useMemo(() => audio.filter((placement) => placement.kind === "bgm"), [audio]);

  // 链接带了时间点：播放头移过去（暂停在那一刻），并选中落在该时间的片段。
  const { seek } = playback;
  useEffect(() => {
    if (!seekRequest) return;
    if (seekRequest.seconds !== null) {
      seek(seekRequest.seconds);
      const location = locate(plan, seekRequest.seconds);
      // eslint-disable-next-line react-hooks/set-state-in-effect -- 一次性的链接定位：移动播放头的同时选中对应片段
      if (location) setSelectedClipId(plan.segments[location.index].clipId);
    }
    // 处理完立即作废，plan 随后的刷新不会再把播放头拉回去。
    onSeekHandled();
  }, [seekRequest, seek, plan, onSeekHandled]);

  const trimIgnored = useMemo(() => issueClipIds(readout, "trim_ignored"), [readout]);
  const unused = useMemo(() => unusedUnitIds(readout), [readout]);
  const thumbnails = useMemo(() => unitThumbnails(script), [script]);
  const activeClipId = plan.segments[playback.index]?.clipId ?? null;
  const currentClip = readout.clips.find((clip) => clip.id === activeClipId);
  const selectedClip = readout.clips.find((clip) => clip.id === selectedClipId);

  return (
    <>
      <EditTimelinePlayer
        plan={plan}
        playback={playback}
        aspect={aspect}
        current={currentClip}
        trimIgnored={currentClip ? trimIgnored.has(currentClip.id) : false}
        subtitles={subtitles}
        showSubtitles={showSubtitles}
        onToggleSubtitles={() => setShowSubtitles((shown) => !shown)}
      />
      <EditTimelineTracks
        projectName={projectName}
        readout={readout}
        t={playback.t}
        selectedClipId={selectedClipId}
        activeClipId={activeClipId}
        trimIgnored={trimIgnored}
        unusedUnits={unused}
        thumbnails={thumbnails}
        narration={narration}
        subtitles={subtitles}
        bgm={bgm}
        onSelectClip={setSelectedClipId}
        onSeek={playback.seek}
      />
      <div className="grid gap-4 md:grid-cols-[1.4fr_1fr]">
        <div className="rounded-[10px] border border-hairline bg-bg-grad-a p-4">
          <ClipInspector
            projectName={projectName}
            clip={selectedClip}
            trimIgnored={selectedClip ? trimIgnored.has(selectedClip.id) : false}
            thumbnail={selectedClip ? thumbnails.get(selectedClip.unit_id) : undefined}
          />
        </div>
        <div className="rounded-[10px] border border-hairline bg-bg-grad-a p-4">
          <IssueList issues={readout.issues} onSelectClip={setSelectedClipId} />
        </div>
      </div>
    </>
  );
}

function Centered({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full min-h-[200px] flex-col items-center justify-center px-6 text-center text-[12.5px] text-text-3">
      {children}
    </div>
  );
}

function LoadFailed({ message, onRetry }: { message: string; onRetry: () => void }) {
  const { t } = useTranslation("dashboard");
  return (
    <Centered>
      <p role="alert" className="text-danger-2">
        {t("edit_view_load_failed", { message })}
      </p>
      <button type="button" onClick={onRetry} className="sv-navbtn mt-3">
        {t("edit_view_retry")}
      </button>
    </Centered>
  );
}
