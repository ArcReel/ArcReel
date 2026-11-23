import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { EpisodeItemRef } from "@/components/shared/episode-item/EpisodeItemRef";
import type { ProblemView } from "./problem-views";

interface Props {
  problems: ProblemView[];
  labelledBy?: string;
  className?: string;
}

export function ProblemList({ problems, labelledBy, className }: Props) {
  const { t } = useTranslation("workflow");
  if (problems.length === 0) return null;
  return (
    <ul aria-labelledby={labelledBy} className={cn("flex flex-col gap-1.5 text-xs", className)}>
      {problems.map((problem) => (
        <li key={problem.key} className="flex flex-col gap-0.5">
          <span className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            {problem.unitId && <EpisodeItemRef itemId={problem.unitId} />}
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
