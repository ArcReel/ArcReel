import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { AlertTriangle, Loader2 } from "lucide-react";

import { API } from "@/api";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { EpisodeMeta, EpisodesView, ReplanAdoptionImpact, ReplanSummary, SourcePoint } from "@/types";
import { errMsg } from "@/utils/async";
import { episodeDisplayName } from "@/utils/episode-display";

import { formatVolume } from "./episodes-view-model";

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
 * 「分集」视图右栏的「新的分集方案」：重新规划生成的候选。生成中显示进度与停止；生成完显示变化摘要、
 * 逐集变化与「放弃新方案」「采纳新方案」。采纳前分集账本不变。
 */
export function ReplanCandidatePanel({ projectName, view, replan, episodes, generating, onChanged }: Props) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [stopRequested, setStopRequested] = useState(false);
  const [busy, setBusy] = useState(false);
  const [discarding, setDiscarding] = useState(false);
  const [adoption, setAdoption] = useState<PendingAdoption | null>(null);
  if (!generating && stopRequested) setStopRequested(false);

  const name = (episode: number) => episodeDisplayName(episodes, episode, t);
  const names = (ids: number[]) => (ids.length === 0 ? t("dashboard:replan_none") : ids.map(name).join(t("dashboard:replan_name_separator")));

  const fail = (err: unknown) =>
    useAppStore.getState().pushToast(t("dashboard:replan_failed", { message: errMsg(err) }), "error");

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
      useAppStore.getState().pushToast(t("dashboard:replan_discard_done"), "success");
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
      useAppStore.getState().pushToast(t("dashboard:replan_adopt_done", { count: response.episodes.length }), "success");
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const shell = (children: ReactNode) => (
    <section
      aria-labelledby="replan-candidate-title"
      className="space-y-2.5 rounded-md py-2 pl-3 pr-2"
      style={{ borderLeft: "3px solid var(--color-accent-2)", background: "var(--color-accent-dim)" }}
    >
      {children}
    </section>
  );

  if (generating) {
    return shell(
      <>
        <h3 id="replan-candidate-title" className="flex items-center gap-1.5 text-[12.5px] font-medium text-text">
          <Loader2 aria-hidden className="h-3.5 w-3.5 text-accent-2 motion-safe:animate-spin" />
          {t("dashboard:replan_generating", { count: replan.new_count })}
        </h3>
        <p className="m-0 text-[11.5px] leading-[1.6] text-text-3">{t("dashboard:replan_generating_hint")}</p>
        {!stopRequested ? (
          <button type="button" className={GHOST_BTN_CLS} onClick={() => void stop()}>
            {t("dashboard:replan_stop")}
          </button>
        ) : null}
      </>,
    );
  }

  const adoptable = replan.stale === null && replan.complete && replan.new_count > 0;
  const notice = replan.stale !== null ? t(`dashboard:replan_stale_${replan.stale}`) : replan.complete ? null : t("dashboard:replan_stopped");

  return shell(
    <>
      <h3 id="replan-candidate-title" className="text-[12.5px] font-medium text-text">
        {t("dashboard:replan_title")}
      </h3>
      <p className="m-0 text-[11.5px] leading-[1.6] text-text-3">
        {t("dashboard:replan_detail", { name: name(replan.episode) })}
      </p>
      {notice ? (
        <p role="status" className="m-0 flex gap-1.5 rounded-md p-2 text-[11.5px] leading-[1.6] text-text-2" style={{ background: "var(--color-warm-soft)" }}>
          <AlertTriangle aria-hidden className="mt-[3px] h-3.5 w-3.5 shrink-0" style={{ color: "var(--color-warm)" }} />
          <span>{notice}</span>
        </p>
      ) : null}
      <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11.5px]">
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
        <div className="space-y-1.5">
          <h4 className="m-0 text-[11.5px] font-medium text-text-2">{t("dashboard:replan_changes")}</h4>
          <ol className="m-0 list-none space-y-1.5 p-0">
            {replan.episodes.map((draft, index) => (
              <li
                key={`${draft.source_file}:${draft.start}`}
                className="rounded-md px-2 py-1.5 text-[11.5px]"
                style={{ background: "oklch(0.2 0.011 265 / 0.55)" }}
              >
                <span className="flex flex-wrap items-baseline gap-x-1.5">
                  <span className="num text-text-3">{t("dashboard:replan_episode_index", { index: index + 1 })}</span>
                  <span className="min-w-0 text-text">{draft.title.trim() || t("dashboard:episodes_view_untitled")}</span>
                  <span className="num text-[10.5px] text-text-4">{formatVolume(t, draft.units, view.unit)}</span>
                </span>
                <span className="mt-0.5 block text-text-3">{relation(t, draft.same_as, draft.overlaps, name, names)}</span>
                {draft.first_sentence ? (
                  <span className="block truncate text-text-4" title={draft.first_sentence}>
                    {t("dashboard:episodes_view_first_sentence", { sentence: draft.first_sentence })}
                  </span>
                ) : null}
                {draft.last_sentence ? (
                  <span className="block truncate text-text-4" title={draft.last_sentence}>
                    {t("dashboard:episodes_view_last_sentence", { sentence: draft.last_sentence })}
                  </span>
                ) : null}
              </li>
            ))}
          </ol>
        </div>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={GHOST_BTN_CLS} disabled={busy} onClick={() => setDiscarding(true)}>
          {t("dashboard:replan_discard")}
        </button>
        {adoptable ? (
          <PrimaryButton size="sm" disabled={busy} onClick={() => void requestAdopt()}>
            {t("dashboard:replan_adopt")}
          </PrimaryButton>
        ) : null}
      </div>
      <ConfirmDialog
        open={discarding}
        title={t("dashboard:replan_discard_title")}
        description={t("dashboard:replan_discard_detail")}
        confirmLabel={t("dashboard:replan_discard")}
        loading={busy}
        onConfirm={discard}
        onCancel={() => setDiscarding(false)}
      />
      {adoption ? (
        <ConfirmDialog
          open
          tone={adoption.deleteRetired ? "danger" : "default"}
          title={t("dashboard:replan_adopt_title")}
          description={
            <>
              {adoption.changed ? (
                <span className="mb-2 block text-[var(--color-warm)]">{t("dashboard:replan_adopt_changed")}</span>
              ) : null}
              <span className="block whitespace-pre-line">{adoption.impact.text}</span>
              {adoption.impact.retired.length > 0 ? (
                <label className="mt-3 flex items-center gap-2 text-text-2">
                  <input
                    type="checkbox"
                    checked={adoption.deleteRetired}
                    disabled={busy}
                    onChange={(e) => setAdoption({ ...adoption, deleteRetired: e.target.checked })}
                  />
                  {t("dashboard:replan_delete_retired")}
                </label>
              ) : null}
              {adoption.deleteRetired ? (
                <span className="mt-2 block whitespace-pre-line text-[var(--color-warm)]">{adoption.impact.delete_text}</span>
              ) : null}
            </>
          }
          confirmLabel={t("dashboard:replan_adopt")}
          loadingLabel={t("dashboard:replan_adopt_running")}
          loading={busy}
          onConfirm={adopt}
          onCancel={() => setAdoption(null)}
        />
      ) : null}
    </>,
  );
}

function SummaryRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-text-4">{label}</dt>
      <dd className="num m-0 text-text-2">{children}</dd>
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
