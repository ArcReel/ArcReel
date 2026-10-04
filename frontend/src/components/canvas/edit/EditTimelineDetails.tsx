import { cn } from "cn";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import type { EditClip, EditTimelineIssue, EditTimelineIssueCode } from "@/types/edit-timeline";

import { formatNameList } from "@/utils/list-format";

import { formatClock, formatSeconds } from "./timeline-view";
import { itemIdWithinEpisode } from "@/utils/episode-display";

const ISSUE_DOT: Record<EditTimelineIssueCode, string> = {
  trim_ignored: "bg-warn",
  unit_deleted: "bg-destructive",
  unit_unused: "bg-muted-foreground",
  video_missing: "bg-destructive",
  hold_too_long: "bg-warn",
  narration_missing: "bg-destructive",
  narration_overrun: "bg-warn",
  narration_source_collision: "bg-warn",
  subtitle_missing_glyphs: "bg-warn",
  bgm_missing: "bg-destructive",
};

interface ClipInspectorProps {
  projectName: string;
  clip: EditClip | undefined;
  trimIgnored: boolean;
  thumbnail: string | undefined;
}

/** 选中片段的位置、截取、转场与说明。截取作废时旧的截取加删除线，并说明暂用完整视频。 */
export function ClipInspector({ projectName, clip, trimIgnored, thumbnail }: ClipInspectorProps) {
  const { t } = useTranslation("dashboard");
  if (!clip) {
    return <p className="text-sm text-muted-foreground">{t("edit_view_inspector_empty")}</p>;
  }
  const deleted = clip.status === "unit_deleted";
  const transition = clip.transition_to_next;
  const sourceLength = clip.source_duration ?? null;
  const trimText = (trim: NonNullable<EditClip["trim"]>) =>
    t("edit_view_trim_value", { in: formatSeconds(trim.source_in), out: formatSeconds(trim.source_out) });
  return (
    <div className="flex flex-col gap-3" data-testid="edit-clip-inspector">
      {thumbnail && (
        <img
          src={API.getFileUrl(projectName, thumbnail)}
          alt=""
          className="aspect-video w-32 shrink-0 rounded-sm bg-black object-contain"
        />
      )}
      <div className="min-w-0 flex-1">
        <h3 className="text-sm font-medium text-foreground">
          {clip.id} <span className="text-muted-foreground">· {itemIdWithinEpisode(clip.unit_id)}</span>
        </h3>
        <dl className="mt-1.5 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs tabular-nums">
          <dt className="text-muted-foreground">{t("edit_view_field_position")}</dt>
          <dd className={deleted ? "text-destructive" : "text-subtle-foreground"}>
            {deleted
              ? t("edit_view_position_deleted")
              : t("edit_view_position_value", {
                  start: formatClock(clip.start),
                  end: formatClock(clip.start + clip.duration),
                  duration: formatSeconds(clip.duration),
                })}
          </dd>
          {!deleted && (
            <>
              <dt className="text-muted-foreground">{t("edit_view_field_trim")}</dt>
              <dd className="text-subtle-foreground">
                {clip.trim ? (
                  trimIgnored ? (
                    // 作废的截取划掉显示，读屏读作删除内容
                    <del className="text-muted-foreground">{trimText(clip.trim)}</del>
                  ) : (
                    <span>{trimText(clip.trim)}</span>
                  )
                ) : (
                  t("edit_view_trim_whole")
                )}
                {sourceLength !== null && t("edit_view_source_length", { duration: formatSeconds(sourceLength) })}
                {trimIgnored && <span className="ml-1.5 text-warn">{t("edit_view_trim_ignored_note")}</span>}
                {clip.status === "video_missing" && (
                  <span className="ml-1.5 text-muted-foreground">{t("edit_view_video_missing_note")}</span>
                )}
              </dd>
              {clip.hold > 0 && (
                <>
                  <dt className="text-muted-foreground">{t("edit_view_field_hold")}</dt>
                  <dd className="text-subtle-foreground">{t("edit_view_hold_value", { duration: formatSeconds(clip.hold) })}</dd>
                </>
              )}
              <dt className="text-muted-foreground">{t("edit_view_field_transition")}</dt>
              <dd className="text-subtle-foreground">
                {transition
                  ? t("edit_view_transition_value", {
                      type: t(`edit_transition_${transition.type}`, { defaultValue: transition.type }),
                      duration: formatSeconds(transition.duration),
                    })
                  : t("edit_view_transition_none")}
              </dd>
            </>
          )}
          {clip.reason && (
            <>
              <dt className="text-muted-foreground">{t("edit_view_field_reason")}</dt>
              <dd className="whitespace-pre-wrap break-words text-subtle-foreground">{clip.reason}</dd>
            </>
          )}
        </dl>
      </div>
    </div>
  );
}

/** 「问题」列表标题的元素 ID，showIssues 据此把焦点带到列表。 */
export const ISSUE_LIST_HEADING_ID = "edit-view-issues-title";

interface IssueListProps {
  issues: readonly EditTimelineIssue[];
  /** 可选中的剪辑片段 ID；BGM 片段（如 b1）不在其中，相关 issue 不带定位按钮。 */
  clipIds: ReadonlySet<string>;
  onSelectClip: (clipId: string) => void;
}

/** 「问题（N）」列表：每条带片段或视频单元编号，指向剪辑片段的点击选中该片段。 */
export function IssueList({ issues, clipIds, onSelectClip }: IssueListProps) {
  const { t, i18n } = useTranslation("dashboard");
  return (
    <section aria-labelledby={ISSUE_LIST_HEADING_ID}>
      <h3
        id={ISSUE_LIST_HEADING_ID}
        tabIndex={-1}
        className="mb-2 rounded-sm text-sm font-medium text-foreground focus-ring"
      >
        {t("edit_view_issues_title", { count: issues.length })}
      </h3>
      {issues.length === 0 ? (
        <p className="text-xs text-muted-foreground">{t("edit_view_issues_none")}</p>
      ) : (
        <ul className="space-y-1">
          {issues.map((issue, index) => {
            const clipId = issue.clip_ids.find((id) => clipIds.has(id));
            const params = {
              ...issue.params,
              clip: formatNameList(issue.clip_ids, i18n.language),
              unit: issue.unit_id ? itemIdWithinEpisode(issue.unit_id) : "",
              code: issue.code,
            };
            // 同一种 issue 按 params.cause 分成几种说法，如旁白越界分「压到下一段旁白」与「超出末尾」。
            const cause = typeof issue.params.cause === "string" ? `_${issue.params.cause}` : "";
            const text = t(`edit_view_issue_${issue.code}${cause}`, {
              ...params,
              defaultValue: t("edit_view_issue_other", params),
            });
            const dot = <span aria-hidden className={cn("mt-1.5 size-1.5 shrink-0 rounded-full", ISSUE_DOT[issue.code] ?? "bg-muted-foreground")} />;
            return (
              <li key={`${issue.code}-${index}`}>
                {clipId ? (
                  <button
                    type="button"
                    onClick={() => onSelectClip(clipId)}
                    className="focus-ring flex w-full items-start gap-2 rounded-sm px-1.5 py-1 text-left text-xs text-subtle-foreground hover:bg-muted"
                  >
                    {dot}
                    {text}
                  </button>
                ) : (
                  <p className="flex items-start gap-2 px-1.5 py-1 text-xs text-subtle-foreground">
                    {dot}
                    {text}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
