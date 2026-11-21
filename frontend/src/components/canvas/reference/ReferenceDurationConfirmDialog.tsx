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

/** 一行「剧本档位 → 申请档位」对照，秒数等宽以便多行纵向对齐。 */
function DurationRow({
  showUnit,
  item,
  diffText,
}: {
  /** 批量确认时逐行指认单元；单个确认不重复。 */
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

export function ReferenceDurationConfirmDialog({ open, items, onConfirm, onCancel }: Props) {
  const { t } = useTranslation("dashboard");
  const durationItems = items.filter((item) => item.precheck.needs_confirmation);
  if (durationItems.length === 0) return null;

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

  const single = durationItems.length === 1 ? durationItems[0] : null;

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
  ) : (
    <div className="space-y-2">
      <p>{t("reference_duration_batch_summary", { count: durationItems.length })}</p>
      <ul className="space-y-1">
        {durationItems.map((item) => (
          <DurationRow key={item.unitId} showUnit item={item} diffText={diffText(item)} />
        ))}
      </ul>
      <p>{t("reference_duration_note_no_trim")}</p>
    </div>
  );
  const title = t("reference_duration_confirm_title");
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
        {/* 多单元的对照行可能很长，正文可以用键盘滚动 */}
        <AlertDialogBody tabIndex={0} role="region" aria-label={title}>
          <AlertDialogDescription render={<div />}>{durationSection}</AlertDialogDescription>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm}>
            {t("reference_duration_confirm_cta")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
