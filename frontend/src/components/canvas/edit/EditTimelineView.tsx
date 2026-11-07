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

/** Current timeline supplied to the tab-row right action area. */
export interface EditTimelineActionsContext {
  timelineId: string;
  timelineName: string;
  /** Issues from the current readout, null until reading completes. */
  issues: readonly EditTimelineIssue[] | null;
  /** Scroll/focus the issues list. */
  showIssues: () => void;
}

export interface EditTimelineEmptyStateContext {
  /** After timeline creation, reload the list and select the most recently modified one. */
  reload: () => void;
}

interface EditTimelineViewProps {
  projectName: string;
  episode: number;
  /** Episode script for unit source-video paths/thumbnails. */
  script: unknown;
  aspect: PreviewAspect;
  /** Whether project narration uses TTS; otherwise post-production voiceover, without missing-audio treatment on narration tracks. */
  ttsNarration: boolean;
  /** Tab-row right action area; omitted means no rendering. */
  renderActions?: (context: EditTimelineActionsContext) => ReactNode;
  /** Empty state when this episode has no timelines. */
  renderEmptyState: (context: EditTimelineEmptyStateContext) => ReactNode;
}

/** One-shot in-app link target: timeline (default selected) and global seek second (default unchanged playhead). */
interface LinkJump {
  timelineId: string | null;
  seconds: number | null;
}

type Loaded<T> = { key: string; value: T } | { key: string; error: string };

/**
 * Episode editing view: read-only timeline preview with rename/delete tab menus, realtime timeline-assembled player above, horizontal tracks below;
 * reread on project changes, including Agent-written revisions.
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

  // Remove tl / t immediately after reading: one-shot positioning must not repeat on page refresh.
  const [searchParams, setSearchParams] = useSearchParams();
  // On project navigation, URL changes before current project; these parameters belong to the incoming view, so do not consume here.
  const routeProject = useParams<{ projectName?: string }>().projectName;
  const linkForOtherProject = routeProject !== undefined && routeProject !== projectName;
  const linkTimeline = searchParams.get(LINK_TIMELINE_PARAM);
  const linkTime = searchParams.get(LINK_TIME_PARAM);
  const [jump, setJump] = useState<LinkJump | null>(null);
  useEffect(() => {
    if (linkForOtherProject || (linkTimeline === null && linkTime === null)) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Subscribe to one-shot URL parameters, clearing after reading so the same link can retrigger
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
  // After deletion, select the default most recently modified timeline.
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
        // Fix initial default selection; later timeline creations/edits must not switch the currently viewed tab.
        setChosenId((previous) => previous ?? pickDefaultTimeline(timelines));
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) setList({ key: listKey, error: errMsg(cause) });
      });
    return () => controller.abort();
  }, [episode, listKey, projectName]);

  // Retain previous lists during refresh so every project change does not flash loading.
  const timelines = list && "value" in list ? list.value : null;

  // Only the loaded list can prove link-target presence: select it if present, otherwise notify and abandon positioning.
  const jumpTimelineId = jump?.timelineId ?? null;
  const jumpTarget =
    timelines !== null && jumpTimelineId !== null && timelines.some((item) => item.id === jumpTimelineId)
      ? jumpTimelineId
      : null;
  // Retained old lists may omit newly created timelines; only this round's loaded list proves absence.
  const listFresh = list?.key === listKey;
  // react-doctor-disable-next-line react-doctor/no-effect-chain -- URL positioning and asynchronous list must both arrive before absence is known; one-time notices are side effects requiring effects
  useEffect(() => {
    if (!jump || !timelines) return;
    if (timelines.length === 0) {
      // react-doctor-disable-next-line react-doctor/no-adjust-state-on-prop-change -- Positioning comes from the URL and lists arrive asynchronously; invalidate only when both are available
      setJump(null); // eslint-disable-line react-hooks/set-state-in-effect -- No target timeline; invalidate this positioning request
      return;
    }
    if (jumpTimelineId !== null && jumpTarget === null && listFresh) {
      useAppStore.getState().pushToast(t("edit_view_link_timeline_missing"), "warning");
      // react-doctor-disable-next-line react-doctor/no-adjust-state-on-prop-change -- Same one-shot notification as above: notify once and invalidate positioning when the target is absent
      setJump(null);
    }
  }, [jump, jumpTimelineId, jumpTarget, listFresh, timelines, t]);
  // After positioning, record the linked timeline as selected so clearing instructions does not revert tabs.
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

  // Reread every project change: readouts depend on units' current media versions, not revisions alone.
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
        // Failed refresh preserves this timeline's previous readout, avoiding playback interruption.
        setReadout((previous) =>
          previous?.timelineId === selectedId && "value" in previous
            ? previous
            : { key: readoutKey, timelineId: selectedId, error: errMsg(cause) },
        );
      });
    return () => controller.abort();
  }, [projectName, readoutKey, selectedId]);

  // Refresh narration/subtitles with readouts; retain on failure, or omit narration/subtitles before the first result.
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
    // Manual tab changes abandon unfinished link positioning.
    setJump(null);
  };
  const authorName = t(`edit_view_author_${selected.updated_by.kind}`);
  const updatedJustNow = isJustNow(selected.updated_at);
  const updatedAt = formatRelativeTime(selected.updated_at, i18n.language) ?? selected.updated_at;

  // Overall view does not scroll: fixed-height tabs, remaining space split among player/details/tracks; only details scroll.
  return (
    <div className="@container-size/edit-view flex h-full min-h-0 flex-col">
      <div className="flex h-12 shrink-0 items-center gap-2 px-4 edit-short:h-10">
        {/* Long/many timeline names scroll horizontally without increasing tab-row height. */}
        <div className="relative flex min-w-0 overflow-x-auto scroll-fade-x">
          {/* Arrow focus switches tabs immediately; switching changes preview only without unmounting other content. */}
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
        // Player/details/tracks stay dark; light backgrounds interfere with judging picture brightness.
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
  /** Requested link position; call `onSeekHandled` afterward. */
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

  // Identical rereads retain plan references so unrelated project changes do not seek playback.
  const planJson = useMemo(() => JSON.stringify(buildPlaybackPlan(readout, media)), [readout, media]);
  const plan = useMemo(() => JSON.parse(planJson) as PlaybackPlan, [planJson]);
  // Source URLs change only with unit current versions; script objects change each refresh, so derive only their shape here.
  const referenceVideo = isReferenceVideoScript(script);
  const sourceUrl = useCallback(
    (segment: PlaybackSegment) =>
      segment.hasVideo && segment.videoVersion !== null
        ? API.getFileUrl(projectName, unitVideoPath(referenceVideo, segment.unitId), segment.videoVersion)
        : null,
    [projectName, referenceVideo],
  );
  // Deduplicate audio placement/narration URLs by content too, preventing identical refreshes from interrupting audio.
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
      // BGM registers by bytes (bgm/{随机 id}) and never rewrites files; unique URLs explicitly omit version numbers.
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

  // Timed links seek the playhead, paused there, and select the clip at that time.
  const { seek } = playback;
  useEffect(() => {
    if (!seekRequest) return;
    if (seekRequest.seconds !== null) {
      seek(seekRequest.seconds);
      const location = locate(plan, seekRequest.seconds);
      // eslint-disable-next-line react-hooks/set-state-in-effect -- One-shot link positioning seeks the playhead and selects its corresponding clip
      if (location) setSelectedClipId(plan.segments[location.index].clipId);
    }
    // Invalidate immediately after handling so later plan refreshes do not seek back.
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

  // Player left, details right, tracks full-width below; details use 280px/340px according to canvas width.
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
      {/* Details are the sole scroll region and may contain only explanatory text; focusability enables keyboard scrolling. */}
      <div
        role="region"
        aria-label={t("edit_view_details_aria")}
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- Details may contain only explanatory text and must support keyboard focus/scroll
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
