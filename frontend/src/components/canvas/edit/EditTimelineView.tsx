import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import type { EditTimelineIssue, EditTimelineReadout, EditTimelineSummary } from "@/types/edit-timeline";
import { errMsg } from "@/utils/async";
import { formatRelativeTime } from "@/utils/date-format";
import type { PreviewAspect } from "@/utils/preview-aspect";

import { ClipInspector, ISSUE_LIST_HEADING_ID, IssueList } from "./EditTimelineDetails";
import { EditTimelineMenu } from "./EditTimelineMenu";
import { EditTimelinePlayer } from "./EditTimelinePlayer";
import { EditTimelineTracks } from "./EditTimelineTracks";
import { buildPlaybackPlan, type PlaybackPlan, type PlaybackSegment } from "./playback-schedule";
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
  /** 标签行右侧的操作区；不传时不渲染。 */
  renderActions?: (context: EditTimelineActionsContext) => ReactNode;
  /** 本集没有剪辑时间线时的空状态；不传时显示说明文字。 */
  renderEmptyState?: (context: EditTimelineEmptyStateContext) => ReactNode;
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

  if (list && "error" in list && !timelines) {
    return <LoadFailed message={list.error} onRetry={reload} />;
  }
  if (!timelines) return <Centered>{t("edit_view_loading")}</Centered>;
  if (timelines.length === 0 || !selected) {
    return renderEmptyState ? (
      renderEmptyState({ reload })
    ) : (
      <Centered>
        <p className="text-[14px] font-medium text-text">{t("edit_view_empty_title")}</p>
        <p className="mt-1 text-[12.5px] text-text-3">{t("edit_view_empty_hint")}</p>
      </Centered>
    );
  }

  const current = readout?.timelineId === selected.id ? readout : null;
  const authorName = t(`edit_view_author_${selected.updated_by.kind}`);
  const updatedAt = formatRelativeTime(selected.updated_at, i18n.language) ?? selected.updated_at;

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto px-6 py-5">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center rounded-[9px] border border-hairline bg-bg-grad-a/55 p-0.5">
          <div role="tablist" aria-label={t("edit_view_timelines_aria")} className="flex flex-wrap">
            {timelines.map((item) => (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={item.id === selected.id}
                onClick={() => setChosenId(item.id)}
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
          {t("edit_view_updated", { author: authorName, time: updatedAt })}
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

      {current && "value" in current ? (
        <TimelinePreview
          key={selected.id}
          projectName={projectName}
          readout={current.value}
          script={script}
          aspect={aspect}
        />
      ) : current && "error" in current ? (
        <LoadFailed message={current.error} onRetry={reload} />
      ) : (
        <Centered>{t("edit_view_loading")}</Centered>
      )}
    </div>
  );
}

interface TimelinePreviewProps {
  projectName: string;
  readout: EditTimelineReadout;
  script: unknown;
  aspect: PreviewAspect;
}

function TimelinePreview({ projectName, readout, script, aspect }: TimelinePreviewProps) {
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);

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
  const playback = useTimelinePlayback(plan, sourceUrl);

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
