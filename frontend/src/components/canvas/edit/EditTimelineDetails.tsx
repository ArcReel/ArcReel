import { cn } from "cn";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import type { EditClip, EditTimelineIssue, EditTimelineIssueCode } from "@/types/edit-timeline";

import { formatNameList } from "@/utils/list-format";

import { formatClock, formatSeconds, unitPositionLabel } from "./timeline-view";

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
  /** Video-unit ID → current position in this episode. */
  positions: ReadonlyMap<string, number>;
}

/** Selected clip position, trim, transition, and explanation; strike invalid trims and explain temporary full-video use. */
export function ClipInspector({ projectName, clip, trimIgnored, thumbnail, positions }: ClipInspectorProps) {
  const { t } = useTranslation("dashboard");
  const thumbnailFp = useProjectsStore((s) => (thumbnail ? s.getAssetFingerprint(thumbnail) : null));
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
          src={API.getFileUrl(projectName, thumbnail, thumbnailFp, { width: 320 })}
          alt=""
          decoding="async"
          className="aspect-video w-32 shrink-0 rounded-sm bg-black object-contain"
        />
      )}
      <div className="min-w-0 flex-1">
        <h3 className="text-sm font-medium text-foreground">
          {clip.id} <span className="text-muted-foreground">· {unitPositionLabel(positions, clip.unit_id, t)}</span>
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
                    // Strike invalid trims; screen readers read deleted content.
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

/** Issues-list heading element ID, used by showIssues to focus the list. */
export const ISSUE_LIST_HEADING_ID = "edit-view-issues-title";

interface IssueListProps {
  issues: readonly EditTimelineIssue[];
  /** Selectable editing-clip IDs, excluding BGM clips such as b1; their issues have no locate button. */
  clipIds: ReadonlySet<string>;
  /** Video-unit ID → current position in this episode. */
  positions: ReadonlyMap<string, number>;
  onSelectClip: (clipId: string) => void;
}

/** Issues (N) list: each identifies a clip number or video-unit position; clip issues select that clip on click. */
export function IssueList({ issues, clipIds, positions, onSelectClip }: IssueListProps) {
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
              unit: issue.unit_id ? (positions.get(issue.unit_id) ?? t("common:deleted")) : "",
              code: issue.code,
            };
            // params.cause distinguishes wording for one issue kind, e.g. narration overlapping the next narration versus exceeding the end.
            const cause = typeof issue.params.cause === "string" ? `_${issue.params.cause}` : "";
            const text = t(`edit_view_issue_${issue.code}${cause}`, {
              ...params,
              defaultValue: t("edit_view_issue_other", params),
            });
            const dot = <span aria-hidden className={cn("mt-1.5 size-1.5 shrink-0 rounded-full", ISSUE_DOT[issue.code] ?? "bg-muted-foreground")} />;
            return (
              // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- Server computes the whole issue list per revision without IDs; codes may repeat, rows are stateless, position identifies them
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
