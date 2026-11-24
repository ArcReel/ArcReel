import { useCallback, useEffect, useId, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useParams, useSearchParams } from "wouter";

import { API } from "@/api";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
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
  unitPositions,
  unitThumbnails,
  unitVideoPath,
  unusedUnitIds,
} from "./timeline-view";
import { useTimelinePlayback } from "./useTimelinePlayback";

export interface EditTimelineActionsContext {
  timelineId: string;
  timelineName: string;
  issues: readonly EditTimelineIssue[] | null;
  showIssues: () => void;
}

export interface EditTimelineEmptyStateContext {
  reload: () => void;
}

interface EditTimelineViewProps {
  projectName: string;
  episode: number;
  script: unknown;
  aspect: PreviewAspect;
  ttsNarration: boolean;
  renderActions?: (context: EditTimelineActionsContext) => ReactNode;
  renderEmptyState: (context: EditTimelineEmptyStateContext) => ReactNode;
}

interface LinkJump {
  timelineId: string | null;
  seconds: number | null;
}

type Loaded<T> = { key: string; value: T } | { key: string; error: string };

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

  const [searchParams, setSearchParams] = useSearchParams();
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
        setChosenId((previous) => previous ?? pickDefaultTimeline(timelines));
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) setList({ key: listKey, error: errMsg(cause) });
      });
    return () => controller.abort();
  }, [episode, listKey, projectName]);

  const timelines = list && "value" in list ? list.value : null;

  const jumpTimelineId = jump?.timelineId ?? null;
  const jumpTarget =
    timelines !== null && jumpTimelineId !== null && timelines.some((item) => item.id === jumpTimelineId)
      ? jumpTimelineId
      : null;
  const listFresh = list?.key === listKey;
  // react-doctor-disable-next-line react-doctor/no-effect-chain -- 地址栏的定位指令与异步读到的列表齐备后才能判定目标不存在，需要提示一次，提示是副作用只能放在 effect
  useEffect(() => {
    if (!jump || !timelines) return;
    if (timelines.length === 0) {
      // react-doctor-disable-next-line react-doctor/no-adjust-state-on-prop-change -- 定位指令来自地址栏、列表异步到达，两者齐备时才能判定作废
      setJump(null); // eslint-disable-line react-hooks/set-state-in-effect -- 没有剪辑时间线可定位，作废这次定位
      return;
    }
    if (jumpTimelineId !== null && jumpTarget === null && listFresh) {
      useAppStore.getState().pushToast(t("edit_view_link_timeline_missing"), "warning");
      // react-doctor-disable-next-line react-doctor/no-adjust-state-on-prop-change -- 与上面的提示同属一次性通知：目标不存在时提示一次并作废定位
      setJump(null);
    }
  }, [jump, jumpTimelineId, jumpTarget, listFresh, timelines, t]);
  const handleJumpApplied = useCallback(() => {
    if (jumpTarget !== null) setChosenId(jumpTarget);
    setJump(null);
  }, [jumpTarget]);
  const selectedId =
    jumpTarget ??
    (timelines && chosenId && timelines.some((item) => item.id === chosenId)
      ? chosenId
      : timelines
        ? pickDefaultTimeline(timelines)
        : null);
  const selected = timelines?.find((item) => item.id === selectedId);

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
        setReadout((previous) =>
          previous?.timelineId === selectedId && "value" in previous
            ? previous
            : { key: readoutKey, timelineId: selectedId, error: errMsg(cause) },
        );
      });
    return () => controller.abort();
  }, [projectName, readoutKey, selectedId]);

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
    setJump(null);
  };
  const authorName = t(`edit_view_author_${selected.updated_by.kind}`);
  const updatedJustNow = isJustNow(selected.updated_at);
  const updatedAt = formatRelativeTime(selected.updated_at, i18n.language) ?? selected.updated_at;

  return (
    <div className="@container-size/edit-view flex h-full min-h-0 flex-col">
      <div className="flex h-12 shrink-0 items-center gap-2 px-4 edit-short:h-10">
        <div className="relative flex min-w-0 overflow-x-auto scroll-fade-x">
          <Tabs value={selected.id} onValueChange={(value: string) => choose(value)} className="shrink-0">
            <TabsList aria-label={t("edit_view_timelines_aria")} activateOnFocus>
              {timelines.map((item) => (
                <TabsTrigger key={item.id} value={item.id} id={tabId(item.id)} aria-controls={panelId}>
                  {item.name}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </div>
        <EditTimelineMenu projectName={projectName} timeline={selected} onRenamed={reload} onDeleted={handleDeleted} />
        <TruncatedText
          className="min-w-0 text-xs text-muted-foreground"
          text={
            updatedJustNow
              ? t("edit_view_updated_just_now", { author: authorName })
              : t("edit_view_updated", { author: authorName, time: updatedAt })
          }
        />
        {renderActions?.({
          timelineId: selected.id,
          timelineName: selected.name,
          issues: current && "value" in current ? current.value.issues : null,
          showIssues,
        })}
      </div>

      <div
        role="tabpanel"
        id={panelId}
        aria-labelledby={tabId(selected.id)}
        className="dark flex min-h-0 flex-1 flex-col bg-background px-4 pb-4 text-foreground edit-short:pb-2"
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
  const { t } = useTranslation("dashboard");
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const [showSubtitles, setShowSubtitles] = useState(true);

  const planJson = useMemo(() => JSON.stringify(buildPlaybackPlan(readout, media)), [readout, media]);
  const plan = useMemo(() => JSON.parse(planJson) as PlaybackPlan, [planJson]);
  const referenceVideo = isReferenceVideoScript(script);
  const sourceUrl = useCallback(
    (segment: PlaybackSegment) =>
      segment.hasVideo && segment.videoVersion !== null
        ? API.getFileUrl(projectName, unitVideoPath(referenceVideo, segment.unitId), segment.videoVersion)
        : null,
    [projectName, referenceVideo],
  );
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
      if (placement.kind === "bgm") {
        const path = sources.bgm[placement.sourceId];
        return path ? API.getFileUrl(projectName, path, null) : null;
      }
      const source = sources.narration[placement.sourceId];
      return source ? API.getFileUrl(projectName, source.path, source.version) : null;
    };
  }, [audioSourcesJson, projectName]);
  const playback = useTimelinePlayback(plan, sourceUrl, audio, audioUrl);
  const narration = useMemo(() => narrationSpans(readout, ttsNarration), [readout, ttsNarration]);
  const subtitles = useMemo(() => placeSubtitles(readout, plan, media), [readout, plan, media]);
  const bgm = useMemo(() => audio.filter((placement) => placement.kind === "bgm"), [audio]);

  const { seek } = playback;
  useEffect(() => {
    if (!seekRequest) return;
    if (seekRequest.seconds !== null) {
      seek(seekRequest.seconds);
      const location = locate(plan, seekRequest.seconds);
      // eslint-disable-next-line react-hooks/set-state-in-effect -- 一次性的链接定位：移动播放头的同时选中对应片段
      if (location) setSelectedClipId(plan.segments[location.index].clipId);
    }
    onSeekHandled();
  }, [seekRequest, seek, plan, onSeekHandled]);

  const trimIgnored = useMemo(() => issueClipIds(readout, "trim_ignored"), [readout]);
  const unused = useMemo(() => unusedUnitIds(readout), [readout]);
  const thumbnails = useMemo(() => unitThumbnails(script), [script]);
  const positions = useMemo(() => unitPositions(script), [script]);
  const activeClipId = plan.segments[playback.index]?.clipId ?? null;
  const currentClip = readout.clips.find((clip) => clip.id === activeClipId);
  const selectedClip = readout.clips.find((clip) => clip.id === selectedClipId);
  const clipIds = useMemo(() => new Set(readout.clips.map((clip) => clip.id)), [readout]);

  return (
    <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_280px] grid-rows-[minmax(0,1fr)_auto] gap-3 edit-short:gap-2 @6xl/canvas:grid-cols-[minmax(0,1fr)_340px]">
      <div className="min-h-0 min-w-0">
        <EditTimelinePlayer
          plan={plan}
          playback={playback}
          aspect={aspect}
          current={currentClip}
          positions={positions}
          trimIgnored={currentClip ? trimIgnored.has(currentClip.id) : false}
          subtitles={subtitles}
          showSubtitles={showSubtitles}
          onToggleSubtitles={() => setShowSubtitles((shown) => !shown)}
        />
      </div>
      <div
        role="region"
        aria-label={t("edit_view_details_aria")}
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- 详情栏可能只有说明文字，须能用键盘聚焦后滚动
        tabIndex={0}
        className="focus-ring relative flex min-h-0 flex-col gap-4 overflow-y-auto rounded-lg border border-border bg-card p-4"
      >
        <section className="shrink-0" aria-label={t("edit_view_inspector_aria")}>
          <ClipInspector
            projectName={projectName}
            clip={selectedClip}
            trimIgnored={selectedClip ? trimIgnored.has(selectedClip.id) : false}
            thumbnail={selectedClip ? thumbnails.get(selectedClip.unit_id) : undefined}
            positions={positions}
          />
        </section>
        <div className="shrink-0 border-t border-border pt-4">
          <IssueList issues={readout.issues} clipIds={clipIds} positions={positions} onSelectClip={setSelectedClipId} />
        </div>
      </div>
      <div className="col-span-2 min-w-0">
        <EditTimelineTracks
          projectName={projectName}
          readout={readout}
          t={playback.t}
          selectedClipId={selectedClipId}
          activeClipId={activeClipId}
          trimIgnored={trimIgnored}
          unusedUnits={unused}
          positions={positions}
          thumbnails={thumbnails}
          narration={narration}
          subtitles={subtitles}
          bgm={bgm}
          onSelectClip={setSelectedClipId}
          onSeek={playback.seek}
        />
      </div>
    </div>
  );
}

function Centered({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 px-6 text-center text-sm text-muted-foreground">
      {children}
    </div>
  );
}

function LoadFailed({ message, onRetry }: { message: string; onRetry: () => void }) {
  const { t } = useTranslation("dashboard");
  return (
    <Centered>
      <p role="alert" className="text-destructive">
        {t("edit_view_load_failed", { message })}
      </p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        {t("edit_view_retry")}
      </Button>
    </Centered>
  );
}
