import { useTranslation } from "react-i18next";
import { UnitTag } from "./UnitTag";
import type { ProblemView } from "./problem-views";

interface Props {
  problems: ProblemView[];
  /** 列表的无障碍名，通常绑到上方的小标题。 */
  labelledBy?: string;
  className?: string;
}

/**
 * 逐条问题的清单。每行按「在哪 · 什么原因 · 下一步」固定顺序陈述，服务端原文收进
 * `<details>`——先给一句能读懂的，排障细节要展开才出现，两者不争同一行。
 *
 * 这里只陈述，不提供动作按钮：动作属于步骤，由步骤自己按后端给的 `next_action` 呈现。
 * 问题行长出按钮就等于界面在替用户推断下一步，那正是这个面板要避免的事。
 */
export function ProblemList({ problems, labelledBy, className }: Props) {
  const { t } = useTranslation("workflow");
  if (problems.length === 0) return null;
  return (
    <ul aria-labelledby={labelledBy} className={className ?? "space-y-2"}>
      {problems.map((problem) => (
        <li key={problem.key} className="flex flex-col gap-0.5">
          <span className="flex flex-wrap items-baseline gap-x-2">
            {problem.unitId && <UnitTag unitId={problem.unitId} />}
            {problem.field && (
              <code
                translate="no"
                className="rounded px-1 font-mono text-[11px]"
                style={{ background: "var(--color-surface-2)", color: "var(--color-text-3)" }}
              >
                {problem.field}
              </code>
            )}
            <span style={{ color: "var(--color-text-2)" }}>{problem.summary}</span>
          </span>
          {problem.meta && (
            <span className="tabular-nums text-[11.5px]" style={{ color: "var(--color-text-3)" }}>
              {problem.meta}
            </span>
          )}
          {problem.nextStep && (
            <span className="text-[11.5px]" style={{ color: "var(--color-text-3)" }}>
              {problem.nextStep}
            </span>
          )}
          {problem.detail && problem.detail !== problem.summary && (
            <details className="text-[11.5px]">
              <summary
                className="focus-ring cursor-pointer select-none rounded"
                style={{ color: "var(--color-text-4)" }}
              >
                {t("technical_detail")}
              </summary>
              <p
                className="mt-1 break-words font-mono text-[11px]"
                style={{ color: "var(--color-text-3)" }}
              >
                {problem.detail}
              </p>
            </details>
          )}
        </li>
      ))}
    </ul>
  );
}
