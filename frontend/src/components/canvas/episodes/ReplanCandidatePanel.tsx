import { useId, useState, type ReactNode, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { AlertTriangle, Loader2 } from "lucide-react";

import { API } from "@/api";
import { EPISODE_PLANNING_SLOTS, enqueueEpisodeReplanContinue } from "@/actions/generation";
import { OutputTruncationHint } from "@/components/shared/OutputTruncationHint";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { isResourceBusy, useTasksStore } from "@/stores/tasks-store";
import type { EpisodeMeta, EpisodesView, ReplanAdoptionImpact, ReplanSummary, SourcePoint } from "@/types";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { errMsg } from "@/utils/async";
import { episodeDisplayName } from "@/utils/episode-display";

import { lastPlanningFailure } from "./episode-planning-model";
import { ImpactConfirmDialog } from "./ImpactConfirmDialog";
import { episodeHue, formatVolume } from "./episodes-view-model";

interface Props {
  projectName: string;
  view: EpisodesView;
  replan: ReplanSummary;
  episodes: EpisodeMeta[];
  /** 方案还在逐窗生成。 */
  generating: boolean;
  onChanged: () => void;
}

interface PendingAdoption {
  impact: ReplanAdoptionImpact & { text: string; delete_text: string };
  deleteRetired: boolean;
  /** 确认时服务端的后果已变，这是更新后的后果。 */
  changed: boolean;
}

/**
 * 「分集」视图方案栏的「新的分集方案」：重新规划生成的候选。生成中显示进度与停止；生成完显示变化摘要、
 * 逐集变化与「放弃新方案」「采纳新方案」。中途停止时说明停在哪里、为什么停，可以继续生成、采纳已完成的部分
 * 或放弃，摘要点名超出方案范围、同样被替换的集。采纳前分集账本不变。
 */
export function ReplanCandidatePanel({ projectName, view, replan, episodes, generating, onChanged }: Props) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [stopRequested, setStopRequested] = useState(false);
  const [busy, setBusy] = useState(false);
  const [discarding, setDiscarding] = useState(false);
  const [adoption, setAdoption] = useState<PendingAdoption | null>(null);
  const tasks = useTasksStore((s) => s.tasks);
  const deleteRetiredId = useId();
  if (!generating && stopRequested) setStopRequested(false);

  const name = (episode: number) => episodeDisplayName(episodes, episode, t);
  const names = (ids: number[]) => (ids.length === 0 ? t("dashboard:replan_none") : ids.map(name).join(t("dashboard:replan_name_separator")));

  const fail = (err: unknown) =>
    useAppStore.getState().pushToast(t("dashboard:replan_failed", { message: errMsg(err) }), "error");

  const continueGenerating = async () => {
    if (EPISODE_PLANNING_SLOTS.some((slot) => isResourceBusy("text_episode_plan", projectName, slot))) {
      useAppStore.getState().pushToast(t("dashboard:episode_planning_busy"), "error");
      return;
    }
    setBusy(true);
    try {
      await enqueueEpisodeReplanContinue(projectName, replan.id);
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    setStopRequested(true);
    try {
      await API.stopEpisodePlanning(projectName);
    } catch (err) {
      setStopRequested(false);
      fail(err);
    }
  };

  const discard = async () => {
    setBusy(true);
    try {
      await API.discardEpisodeReplan(projectName, replan.id);
      setDiscarding(false);
      onChanged();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const requestAdopt = async () => {
    setBusy(true);
    try {
      const response = await API.adoptEpisodeReplan(projectName, replan.id);
      if (response.status === "confirmation_required") {
        setAdoption({ impact: response.impact, deleteRetired: false, changed: false });
      }
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const adopt = async () => {
    if (adoption === null) return;
    setBusy(true);
    try {
      const response = await API.adoptEpisodeReplan(projectName, replan.id, {
        revision: adoption.impact.revision,
        deleteRetired: adoption.deleteRetired,
      });
      if (response.status === "confirmation_required") {
        setAdoption({ ...adoption, impact: response.impact, changed: true });
        return;
      }
      setAdoption(null);
      onChanged();
      await useProjectsStore.getState().refreshProject(projectName);
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const shell = (children: ReactNode) => <div className="flex flex-col gap-3 text-xs">{children}</div>;

  if (generating) {
    return shell(
      <>
        <h2 className="flex items-center gap-1.5 text-sm font-medium text-foreground">
          <Loader2 aria-hidden className="size-3.5 animate-spin text-primary" />
          {t("dashboard:replan_generating", { count: replan.new_count })}
        </h2>
        <p className="text-muted-foreground">{t("dashboard:replan_generating_hint")}</p>
        <CompareLegend pending />
        {!stopRequested ? (
          <Button variant="outline" size="sm" className="self-start" onClick={() => void stop()}>
            {t("dashboard:replan_stop")}
          </Button>
        ) : null}
      </>,
    );
  }

  const stopped = replan.stale === null && !replan.complete;
  const adoptable = replan.stale === null && replan.new_count > 0;
  let notice: string | null = null;
  if (replan.stale !== null) {
    notice = t(`dashboard:replan_stale_${replan.stale}`);
  } else if (stopped) {
    const reason = replan.interrupted === null ? "dashboard:replan_stopped" : `dashboard:replan_stopped_${replan.interrupted}`;
    notice = t(reason, { end: point(view, replan.end) });
  }
  const truncated =
    stopped && replan.interrupted === "failed" ? (lastPlanningFailure(tasks, projectName)?.truncated ?? null) : null;

  return shell(
    <>
      <h2 className="text-sm font-medium text-foreground">{t("dashboard:replan_title")}</h2>
      <p className="text-muted-foreground">
        {t("dashboard:replan_detail", { name: name(replan.episode) })}
      </p>
      {replan.stale === null ? <CompareLegend pending={stopped} /> : null}
      {notice ? (
        <p role="status" className="flex gap-1.5 rounded-md bg-warn/10 p-2 text-subtle-foreground">
          <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0 text-warn" />
          <span>
            {notice}
            {stopped && adoptable ? <span className="mt-1 block">{t("dashboard:replan_stopped_adopt_hint")}</span> : null}
          </span>
        </p>
      ) : null}
      {truncated ? <OutputTruncationHint truncation={truncated} /> : null}
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
        <SummaryRow label={t("dashboard:replan_count")}>
          {replan.old_count === null
            ? t("dashboard:replan_count_new_only", { count: replan.new_count })
            : t("dashboard:replan_count_value", { old: replan.old_count, new: replan.new_count })}
        </SummaryRow>
        <SummaryRow label={t("dashboard:replan_average")}>
          {replan.average_units === null ? t("dashboard:replan_none") : formatVolume(t, replan.average_units, view.unit)}
        </SummaryRow>
        <SummaryRow label={t("dashboard:replan_coverage")}>
          {t("dashboard:replan_coverage_value", { start: point(view, replan.start), end: point(view, replan.end) })}
        </SummaryRow>
        {replan.stale === null ? (
          <>
            {stopped ? (
              <SummaryRow label={t("dashboard:replan_uncovered")}>{names(replan.uncovered)}</SummaryRow>
            ) : null}
            <SummaryRow label={t("dashboard:replan_needs_review")}>{names(replan.needs_review)}</SummaryRow>
            <SummaryRow label={t("dashboard:replan_retired")}>{names(replan.retired)}</SummaryRow>
            <SummaryRow label={t("dashboard:replan_removed")}>{names(replan.removed)}</SummaryRow>
            <SummaryRow label={t("dashboard:replan_moved")}>
              {replan.moved.length === 0
                ? t("dashboard:replan_none")
                : replan.moved
                    .map((move) => t("dashboard:replan_moved_item", { name: name(move.episode), from: move.from, to: move.to }))
                    .join(t("dashboard:replan_name_separator"))}
            </SummaryRow>
          </>
        ) : null}
        <SummaryRow label={t("dashboard:replan_instructions")}>
          {replan.instructions?.trim() || t("dashboard:replan_none")}
        </SummaryRow>
      </dl>
      {replan.episodes.length > 0 ? (
        <div className="flex flex-col gap-1.5">
          <h3 className="font-medium text-subtle-foreground">{t("dashboard:replan_changes")}</h3>
          <ol className="flex flex-col gap-1.5">
            {replan.episodes.map((draft, index) => (
              <li
                key={`${draft.source_file}:${draft.start}`}
                className="flex flex-col gap-0.5 rounded-md bg-muted/50 px-2 py-1.5"
              >
                <span className="flex flex-wrap items-baseline gap-x-1.5">
                  <span className="num text-muted-foreground">{t("dashboard:replan_episode_index", { index: index + 1 })}</span>
                  <span className="min-w-0 text-foreground">{draft.title.trim() || t("dashboard:episodes_view_untitled")}</span>
                  <span className="num text-muted-foreground">{formatVolume(t, draft.units, view.unit)}</span>
                </span>
                <span className="text-muted-foreground">{relation(t, draft.same_as, draft.overlaps, name, names)}</span>
                {draft.first_sentence ? (
                  <TruncatedText
                    text={t("dashboard:episodes_view_first_sentence", { sentence: draft.first_sentence })}
                    className="text-muted-foreground"
                  />
                ) : null}
                {draft.last_sentence ? (
                  <TruncatedText
                    text={t("dashboard:episodes_view_last_sentence", { sentence: draft.last_sentence })}
                    className="text-muted-foreground"
                  />
                ) : null}
              </li>
            ))}
          </ol>
        </div>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        {adoptable ? (
          <Button size="sm" disabled={busy} onClick={() => void requestAdopt()}>
            {t(stopped ? "dashboard:replan_adopt_partial" : "dashboard:replan_adopt")}
          </Button>
        ) : null}
        {stopped ? (
          <Button variant="outline" size="sm" disabled={busy} onClick={() => void continueGenerating()}>
            {t("dashboard:replan_continue")}
          </Button>
        ) : null}
        <Button variant="outline" size="sm" disabled={busy} onClick={() => setDiscarding(true)}>
          {t("dashboard:replan_discard")}
        </Button>
      </div>
      <ImpactConfirmDialog
        request={
          discarding
            ? {
                title: t("dashboard:replan_discard_title"),
                body: <p>{t("dashboard:replan_discard_detail")}</p>,
                confirmLabel: t("dashboard:replan_discard"),
                destructive: true,
              }
            : null
        }
        busy={busy}
        onConfirm={() => void discard()}
        onCancel={() => setDiscarding(false)}
      />
      <ImpactConfirmDialog
        request={
          adoption
            ? {
                title: t("dashboard:replan_adopt_title"),
                body: (
                  <div className="flex flex-col gap-3">
                    {adoption.changed ? <p className="text-warn">{t("dashboard:replan_adopt_changed")}</p> : null}
                    <p className="whitespace-pre-line">{adoption.impact.text}</p>
                    {adoption.impact.retired.length > 0 ? (
                      <div className="flex items-center gap-2">
                        <Checkbox
                          id={deleteRetiredId}
                          checked={adoption.deleteRetired}
                          disabled={busy}
                          onCheckedChange={(checked) => setAdoption({ ...adoption, deleteRetired: checked })}
                        />
                        <Label htmlFor={deleteRetiredId}>{t("dashboard:replan_delete_retired")}</Label>
                      </div>
                    ) : null}
                    {adoption.deleteRetired ? (
                      <p className="whitespace-pre-line text-warn">{adoption.impact.delete_text}</p>
                    ) : null}
                  </div>
                ),
                confirmLabel: t(stopped ? "dashboard:replan_adopt_partial" : "dashboard:replan_adopt"),
                runningLabel: t("dashboard:replan_adopt_running"),
                destructive: adoption.deleteRetired,
                interactiveBody: adoption.impact.retired.length > 0,
              }
            : null
        }
        busy={busy}
        onConfirm={() => void adopt()}
        onCancel={() => setAdoption(null)}
      />
    </>,
  );
}

/** 原文旁并排色条的图例：左侧现有分集、右侧新方案、琥珀色虚线是分界不同处；`pending` 时加上等待规划。 */
function CompareLegend({ pending }: { pending: boolean }) {
  const { t } = useTranslation("dashboard");
  return (
    <ul className="flex flex-wrap gap-x-3 gap-y-1 text-muted-foreground" aria-label={t("replan_legend")}>
      <li className="flex items-center gap-1.5">
        <span aria-hidden className="flex h-3 w-0.75 shrink-0 flex-col overflow-hidden rounded-full">
          <span className="flex-1 bg-episode" style={{ "--episode-hue": episodeHue(1) } as CSSProperties} />
          <span className="flex-1 bg-episode" style={{ "--episode-hue": episodeHue(2) } as CSSProperties} />
        </span>
        {t("replan_legend_old")}
      </li>
      <li className="flex items-center gap-1.5">
        <span aria-hidden className="flex h-3 w-0.75 shrink-0 flex-col overflow-hidden rounded-full">
          <span className="flex-1 bg-primary" />
          <span className="flex-1 bg-primary/55" />
        </span>
        {t("replan_legend_new")}
      </li>
      <li className="flex items-center gap-1.5">
        <span aria-hidden className="w-3 border-t border-dashed border-warn" />
        {t("replan_legend_diff")}
      </li>
      {pending ? (
        <li className="flex items-center gap-1.5">
          <span aria-hidden className="h-3 border-l-3 border-dashed border-input" />
          {t("replan_waiting")}
        </li>
      ) : null}
    </ul>
  );
}

function SummaryRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-subtle-foreground tabular-nums">{children}</dd>
    </>
  );
}

/** 源文位置的读法：文件名与在这个文件里的百分比。 */
function point(view: EpisodesView, at: SourcePoint): string {
  const file = view.files.find((entry) => entry.source_file === at.source_file);
  if (!file) return at.source_file;
  const percent = file.length === 0 ? 0 : Math.round((at.offset / file.length) * 100);
  return `${file.name} ${percent}%`;
}

function relation(
  t: TFunction,
  sameAs: number | null,
  overlaps: number[],
  name: (episode: number) => string,
  names: (episodes: number[]) => string,
): string {
  if (sameAs !== null) return t("dashboard:replan_episode_same", { name: name(sameAs) });
  if (overlaps.length > 0) return t("dashboard:replan_episode_overlaps", { names: names(overlaps) });
  return t("dashboard:replan_episode_fresh");
}
