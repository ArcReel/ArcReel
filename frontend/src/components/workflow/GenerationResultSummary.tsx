import { useId } from "react";
import { useTranslation } from "react-i18next";
import type { GenerationBatchResult, GenerationItemResult } from "@/types/workflow";
import { ProblemList } from "./ProblemList";
import { UnitTag } from "./UnitTag";
import { ARTIFACT_TONES, taskTone } from "./state-language";
import { problemViews } from "./problem-views";

/** 逐项结果里，任务这一轴与产物这一轴分两句话讲，不合并成一个词。 */
function ItemRow({ item }: { item: GenerationItemResult }) {
  const { t } = useTranslation("workflow");
  const tone = item.state === "succeeded" ? taskTone("succeeded") : ARTIFACT_TONES.blocked;
  return (
    <li className="flex flex-col gap-0.5">
      <span className="flex flex-wrap items-baseline gap-x-2">
        <UnitTag unitId={item.unit_id} />
        <span className="text-[11.5px]" style={{ color: tone.color }}>
          {t(`item_state_${item.state}`)}
        </span>
        <span className="text-[11px]" style={{ color: "var(--color-text-3)" }}>
          {t("item_task_state", {
            state: t(`task_state_${item.task_state}`, { defaultValue: item.task_state }),
          })}
        </span>
        {item.artifact_status && (
          <span className="text-[11px]" style={{ color: ARTIFACT_TONES[item.artifact_status].color }}>
            {t("item_artifact_status", { status: t(`artifact_${item.artifact_status}`) })}
          </span>
        )}
      </span>
      {item.provider_checkpoint?.submitted && (
        <span className="text-[11px]" style={{ color: "var(--color-text-3)" }}>
          {t("checkpoint_submitted", {
            provider: item.provider_checkpoint.provider_id ?? t("checkpoint_provider_unknown"),
          })}
        </span>
      )}
    </li>
  );
}

interface Props {
  result: GenerationBatchResult;
}

/**
 * 上一次批量执行的逐项结果。
 *
 * 后端保证 requested 被 succeeded / failed / blocked 穷尽划分，界面照此逐项列出，
 * 因为「部分成功」正是最容易被一句总结掩盖的情形：只报一句「已提交」，用户不会发现
 * 其中三个单元其实一个都没做成。
 *
 * 每项同时给出任务下场与产物时效两句话。一次尝试失败不等于没有可用产物——旧产物还在，
 * 状态照旧；把两者合成一个词，用户就分不清该重试还是该去修输入。
 */
export function GenerationResultSummary({ result }: Props) {
  const { t } = useTranslation("workflow");
  const headingId = useId();
  const failedProblems = result.items
    .filter((item) => item.state !== "succeeded" && item.problem)
    .map((item) => ({ ...item.problem!, params: { unit_id: item.unit_id, ...item.problem!.params } }));

  return (
    <section aria-labelledby={headingId} className="space-y-1.5">
      <h4 id={headingId} className="text-[12px] font-medium" style={{ color: "var(--color-text-2)" }}>
        {t("result_title", {
          requested: result.requested.length,
          succeeded: result.succeeded.length,
          failed: result.failed.length,
          blocked: result.blocked.length,
        })}
      </h4>
      <ul className="space-y-1">
        {result.items.map((item) => (
          <ItemRow key={item.unit_id} item={item} />
        ))}
      </ul>
      {result.skipped.length > 0 && (
        <p className="text-[11.5px]" style={{ color: "var(--color-text-3)" }}>
          {t("result_skipped", { count: result.skipped.length })}
        </p>
      )}
      {failedProblems.length > 0 && (
        <ProblemList problems={problemViews(t, failedProblems, "result")} className="space-y-1.5" />
      )}
    </section>
  );
}
