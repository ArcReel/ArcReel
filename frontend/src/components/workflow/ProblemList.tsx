import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { UnitTag } from "./UnitTag";
import type { ProblemView } from "./problem-views";

interface Props {
  problems: ProblemView[];
  /** 列表的无障碍名，通常绑到上方的小标题。 */
  labelledBy?: string;
  className?: string;
}

/**
 * 逐条问题的清单。每行按「在哪 · 什么原因 · 下一步」固定顺序陈述；带服务端原文的（数据损坏的
 * 阻断、批量端点的结论）把原文收进 `<details>`——先给一句能读懂的，排障细节要展开才出现。
 *
 * 这里只陈述，不提供动作按钮：动作属于步骤，由步骤自己按后端给的 `next_action` 呈现。
 * 问题行长出按钮就等于界面在替用户推断下一步，那正是这个面板要避免的事。
 */
export function ProblemList({ problems, labelledBy, className }: Props) {
  const { t } = useTranslation("workflow");
  if (problems.length === 0) return null;
  return (
    <ul aria-labelledby={labelledBy} className={cn("flex flex-col gap-1.5 text-xs", className)}>
      {problems.map((problem) => (
        <li key={problem.key} className="flex flex-col gap-0.5">
          <span className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            {problem.unitId && <UnitTag unitId={problem.unitId} />}
            {problem.field && (
              <code translate="no" className="rounded-sm bg-muted px-1 font-mono text-subtle-foreground">
                {problem.field}
              </code>
            )}
            <span className="text-subtle-foreground">{problem.summary}</span>
          </span>
          {problem.meta && <span className="text-subtle-foreground tabular-nums">{problem.meta}</span>}
          {problem.nextStep && <span className="text-subtle-foreground">{problem.nextStep}</span>}
          {problem.detail && problem.detail !== problem.summary && (
            <details>
              <summary className="focus-ring w-fit cursor-pointer rounded-sm text-subtle-foreground select-none">
                {t("technical_detail")}
              </summary>
              <p className="mt-1 font-mono break-words text-subtle-foreground">{problem.detail}</p>
            </details>
          )}
        </li>
      ))}
    </ul>
  );
}
