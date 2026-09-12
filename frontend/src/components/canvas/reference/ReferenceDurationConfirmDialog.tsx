import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import type { ReferenceDurationPrecheck } from "@/types";
import { formatCurrencyAmount } from "@/utils/cost-format";
import { advisoryProblems } from "./advisory-problems";

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
 * 视频入队前的确认：模型只接受离散时长档位，fresh TTS 需要的申请档位一旦偏离当前视觉档位
 * （没有可信成片时偏离剧本档位），就在入队前讲清楚档位与费用，由用户决定是否继续；预检的
 * 非阻断问题（参考图取前 N 张）与取档偏离同等触发本弹窗，列在时长对照行之下——它们改变
 * 成片内容却不拦截入队，事后界面上没有任何展示面。
 *
 * 整个弹窗只覆盖一个单元时直接陈述两个秒数与后果；覆盖多个时逐行列出并带上单元标签，每行
 * 自带更长/更短的差值，混合方向也能一次看清。列表超出高度即滚动而不折叠计数：被折叠的行里
 * 可能有成片更短的单元，而用户是在看不到它的情况下为它拍板。
 */
export function ReferenceDurationConfirmDialog({ open, items, onConfirm, onCancel }: Props) {
  const { t } = useTranslation("dashboard");
  const durationItems = items.filter((item) => item.precheck.needs_confirmation);
  const advisoryItems = items
    .map((item) => ({ unitId: item.unitId, problems: advisoryProblems(item.precheck) }))
    .filter((entry) => entry.problems.length > 0);
  if (durationItems.length === 0 && advisoryItems.length === 0) return null;

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

  // 单元标签的有无按整个弹窗覆盖的单元数决定，不按分区各自的条数：时长行不带标签而
  // 告知行带标签，读者无从判断两处说的是不是同一个单元。
  const single = items.length === 1 && durationItems.length === 1 ? durationItems[0] : null;

  const durationSection = single ? (
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
  ) : durationItems.length > 0 ? (
    <div className="space-y-2">
      <p>{t("reference_duration_batch_summary", { count: durationItems.length })}</p>
      <ul className="max-h-48 space-y-1 overflow-y-auto">
        {durationItems.map((item) => (
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
  ) : null;

  const advisorySection =
    advisoryItems.length > 0 ? (
      <div className="space-y-2">
        <p>{t("reference_advisory_heading")}</p>
        <ul className="max-h-32 space-y-1 overflow-y-auto">
          {advisoryItems.flatMap(({ unitId, problems }) =>
            problems.map((problem, index) => (
              <li
                key={`${unitId}-${index}`}
                className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5"
              >
                {items.length > 1 && (
                  <span
                    className="font-mono text-[11.5px]"
                    style={{ color: "var(--color-text-3)" }}
                  >
                    {unitId}
                  </span>
                )}
                <span style={{ color: "var(--color-text-2)" }}>{problem.message}</span>
              </li>
            )),
          )}
        </ul>
      </div>
    ) : null;

  // 没有告知分区时不套间距容器：单一分区自带内部间距，多包一层只会多出一处空白
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

  return (
    <ConfirmDialog
      open={open}
      title={t(
        durationChanges ? "reference_duration_confirm_title" : "reference_advisory_confirm_title",
      )}
      description={description}
      confirmLabel={t(
        durationChanges ? "reference_duration_confirm_cta" : "reference_advisory_confirm_cta",
      )}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}
