import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import type { ReferenceDurationPrecheck } from "@/types";
import { formatCurrencyAmount } from "@/utils/cost-format";

/** 需确认的单元及其取档结果。 */
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

/** 一行「当前视觉/剧本档位 → 申请档位」对照，秒数等宽以便多行纵向对齐。 */
function DurationRow({
  label,
  item,
  diffText,
}: {
  label: string | null;
  item: DurationConfirmItem;
  diffText: string;
}) {
  const { t } = useTranslation("dashboard");
  const seconds = (value: number) => t("reference_duration_seconds", { value });
  return (
    <li className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
      {label && (
        <span className="font-mono text-[11.5px]" style={{ color: "var(--color-text-3)" }}>
          {label}
        </span>
      )}
      <span className="tabular-nums" style={{ color: "var(--color-text-2)" }}>
        {seconds(item.precheck.current_visual_duration ?? item.precheck.script_duration)}
      </span>
      <span aria-hidden style={{ color: "var(--color-text-4)" }}>
        →
      </span>
      <span className="tabular-nums font-medium" style={{ color: "var(--color-text)" }}>
        {seconds(item.precheck.request_duration)}
      </span>
      <span style={{ color: "var(--color-text-3)" }}>{diffText}</span>
      {item.precheck.duration_input !== (
        item.precheck.current_visual_duration ?? item.precheck.script_duration
      ) && (
        <span className="basis-full text-[11.5px]" style={{ color: "var(--color-text-2)" }}>
          {t("reference_duration_request_basis", {
            duration: seconds(item.precheck.duration_input),
          })}
        </span>
      )}
      {item.precheck.request_cost && (
        <span className="basis-full text-[11.5px]" style={{ color: "var(--color-text-2)" }}>
          {t("reference_duration_request_cost", {
            cost: formatCurrencyAmount(
              item.precheck.request_cost.currency,
              item.precheck.request_cost.amount,
            ),
            provider: item.precheck.request_cost.provider_id,
            model: item.precheck.request_cost.model_id,
            duration: seconds(item.precheck.request_cost.request_duration_seconds),
          })}
        </span>
      )}
    </li>
  );
}

/**
 * 视频入队前的时长确认：模型只接受离散时长档位，fresh TTS 需要的申请档位一旦偏离当前
 * 视觉档位（没有可信成片时偏离剧本档位），就在入队前讲清楚档位与费用，由用户决定是否继续。
 *
 * 单元只有一个时直接陈述两个秒数与后果；批量时逐行列出，每行自带更长/更短的差值，混合
 * 方向也能一次看清。列表超出高度即滚动而不折叠计数：被折叠的行里可能有成片更短的单元，
 * 而用户是在看不到它的情况下为它拍板。
 */
export function ReferenceDurationConfirmDialog({ open, items, onConfirm, onCancel }: Props) {
  const { t } = useTranslation("dashboard");
  if (items.length === 0) return null;

  const seconds = (value: number) => t("reference_duration_seconds", { value });
  const baseline = (item: DurationConfirmItem) =>
    item.precheck.current_visual_duration ?? item.precheck.script_duration;
  const diffText = (item: DurationConfirmItem) => {
    const { request_duration } = item.precheck;
    const previous = baseline(item);
    const diff = Math.abs(request_duration - previous);
    return request_duration < previous
      ? t("reference_duration_diff_shorter", { value: seconds(diff) })
      : t("reference_duration_diff_longer", { value: seconds(diff) });
  };

  const single = items.length === 1 ? items[0] : null;

  const description = single ? (
    <div className="space-y-2">
      <ul className="space-y-1">
        <DurationRow label={null} item={single} diffText={diffText(single)} />
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
      <p>{t("reference_duration_batch_summary", { count: items.length })}</p>
      <ul className="max-h-48 space-y-1 overflow-y-auto">
        {items.map((item) => (
          <DurationRow
            key={item.unitId}
            label={item.unitId}
            item={item}
            diffText={diffText(item)}
          />
        ))}
      </ul>
      <p>{t("reference_duration_note_no_trim")}</p>
    </div>
  );

  return (
    <ConfirmDialog
      open={open}
      title={t("reference_duration_confirm_title")}
      description={description}
      confirmLabel={t("reference_duration_confirm_cta")}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}
