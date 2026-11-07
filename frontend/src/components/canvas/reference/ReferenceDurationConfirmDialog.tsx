import { useTranslation } from "react-i18next";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogBody,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import type { ReferenceDurationPrecheck } from "@/types";
import { EpisodeItemRef } from "@/components/shared/episode-item/EpisodeItemRef";
import { advisoryProblems } from "./advisory-problems";

/** Units requiring confirmation and their chosen duration tiers. */
export interface DurationConfirmItem {
  unitId: string;
  precheck: ReferenceDurationPrecheck;
}

interface Props {
  open: boolean;
  items: DurationConfirmItem[];
  onConfirm: () => void;
  onCancel: () => void;
}

/** Script-tier → requested-tier row, with monospace seconds for vertical alignment. */
function DurationRow({
  showUnit,
  item,
  diffText,
}: {
  /** Batch confirmation identifies units per row; single-unit confirmation does not repeat labels. */
  showUnit: boolean;
  item: DurationConfirmItem;
  diffText: string;
}) {
  const { t } = useTranslation("dashboard");
  const seconds = (value: number) => t("reference_duration_seconds", { value });
  return (
    <li className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
      {showUnit && <EpisodeItemRef itemId={item.unitId} />}
      <span className="tabular-nums text-subtle-foreground">{seconds(item.precheck.script_duration)}</span>
      <span aria-hidden className="text-muted-foreground">
        →
      </span>
      <span className="font-medium tabular-nums text-foreground">{seconds(item.precheck.request_duration)}</span>
      <span className="text-muted-foreground">{diffText}</span>
    </li>
  );
}

/**
 * Preenqueue confirmation: models accept discrete duration tiers. If requested tiers differ from script tiers, explain
 * before enqueue and let users choose whether to proceed. Preflight
 * nonblocking reference truncation (first N images) also triggers this dialog, beneath duration comparisons: it changes
 * final contents without blocking enqueue and has no later UI display surface.
 *
 * Single-unit dialogs state both durations/consequences directly; multiple units list labels per row, each
 * with longer/shorter differences so mixed directions remain clear. Scroll long lists without collapsing counts:
 * collapsed rows could hide shortened results while users decide on them.
 */
export function ReferenceDurationConfirmDialog({ open, items, onConfirm, onCancel }: Props) {
  const { t } = useTranslation("dashboard");
  const durationItems = items.filter((item) => item.precheck.needs_confirmation);
  const advisoryItems = items
    .map((item) => ({ unitId: item.unitId, problems: advisoryProblems(item.precheck) }))
    .filter((entry) => entry.problems.length > 0);
  if (durationItems.length === 0 && advisoryItems.length === 0) return null;

  const seconds = (value: number) => t("reference_duration_seconds", { value });
  const baseline = (item: DurationConfirmItem) => item.precheck.script_duration;
  const diffText = (item: DurationConfirmItem) => {
    const { request_duration } = item.precheck;
    const previous = baseline(item);
    const diff = Math.abs(request_duration - previous);
    return request_duration < previous
      ? t("reference_duration_diff_shorter", { value: seconds(diff) })
      : t("reference_duration_diff_longer", { value: seconds(diff) });
  };

  // Unit labels depend on total dialog units, not per-section counts: unlabeled duration rows but
  // labeled notices would leave readers unable to tell whether both refer to the same unit.
  const single = items.length === 1 && durationItems.length === 1 ? durationItems[0] : null;

  const durationSection = single ? (
    <div className="space-y-2">
      <ul className="space-y-1">
        <DurationRow showUnit={false} item={single} diffText={diffText(single)} />
      </ul>
      <p>
        {single.precheck.request_duration < baseline(single)
          ? t("reference_duration_note_shorter", {
              duration: seconds(single.precheck.request_duration),
            })
          : t("reference_duration_note_longer", {
              duration: seconds(single.precheck.request_duration),
            })}
      </p>
    </div>
  ) : durationItems.length > 0 ? (
    <div className="space-y-2">
      <p>{t("reference_duration_batch_summary", { count: durationItems.length })}</p>
      <ul className="space-y-1">
        {durationItems.map((item) => (
          <DurationRow key={item.unitId} showUnit item={item} diffText={diffText(item)} />
        ))}
      </ul>
      <p>{t("reference_duration_note_no_trim")}</p>
    </div>
  ) : null;

  const advisorySection =
    advisoryItems.length > 0 ? (
      <div className="space-y-2">
        <p>{t("reference_advisory_heading")}</p>
        <ul className="space-y-1">
          {advisoryItems.flatMap(({ unitId, problems }) =>
            problems.map((problem, index) => (
              // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- Server-computed unit notices have no IDs, remain read-only while confirmation is open, and carry no row state
              <li
                key={`${unitId}-${index}`}
                className="flex flex-wrap items-center gap-x-2 gap-y-0.5"
              >
                {items.length > 1 && <EpisodeItemRef itemId={unitId} />}
                <span className="text-subtle-foreground">{problem.message}</span>
              </li>
            )),
          )}
        </ul>
      </div>
    ) : null;

  // Without notices omit spacing wrappers; individual sections already provide spacing, so extras create blank gaps.
  const description =
    advisorySection === null ? (
      durationSection
    ) : (
      <div className="space-y-3">
        {durationSection}
        {advisorySection}
      </div>
    );
  const durationChanges = durationItems.length > 0;

  const title = t(
    durationChanges ? "reference_duration_confirm_title" : "reference_advisory_confirm_title",
  );
  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onCancel();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
        </AlertDialogHeader>
        {/* Multi-unit comparison rows may be long; body supports keyboard scrolling. */}
        <AlertDialogBody tabIndex={0} role="region" aria-label={title}>
          <AlertDialogDescription render={<div />}>{description}</AlertDialogDescription>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm}>
            {t(durationChanges ? "reference_duration_confirm_cta" : "reference_advisory_confirm_cta")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
