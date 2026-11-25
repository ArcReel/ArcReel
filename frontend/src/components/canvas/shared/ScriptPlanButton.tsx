import { useId } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, RotateCcw, Sparkles } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { useScriptPlanEntry } from "@/hooks/useScriptPlanEntry";
import { useScriptPlanStore, type ScriptPlanReplacement } from "@/stores/script-plan-store";

import { TextTaskProgress } from "@/components/shared/TextTaskProgress";

interface Props {
  projectName: string;
  episode: number;
  replaces: ScriptPlanReplacement;
  className?: string;
  disabledReason?: string | null;
}

export function ScriptPlanButton({ projectName, episode, replaces, className, disabledReason }: Props) {
  const { t } = useTranslation("dashboard");
  const reasonId = useId();
  const open = useScriptPlanStore((s) => s.open);
  const { busy, latestTask, refusedReason } = useScriptPlanEntry(projectName, episode);
  const reason = busy ? t("script_plan_busy") : (refusedReason ?? disabledReason ?? null);
  const firstPlan = replaces === "formal_script";
  const Icon = busy ? Loader2 : firstPlan ? Sparkles : RotateCcw;
  return (
    <>
      <span className={cn("inline-flex", reason !== null && "cursor-not-allowed opacity-50")}>
        <Button
          variant="outline"
          size="sm"
          className={className}
          aria-disabled={reason !== null || undefined}
          aria-describedby={reason ? reasonId : undefined}
          title={reason ?? undefined}
          onClick={() => {
            if (reason === null) open({ projectName, episode, replaces });
          }}
        >
          <Icon aria-hidden data-icon="inline-start" className={busy ? "animate-spin" : undefined} />
          {firstPlan ? t("script_plan_open") : t("script_plan_regenerate_open")}
        </Button>
        {reason && (
          <span id={reasonId} className="sr-only">
            {reason}
          </span>
        )}
      </span>
      {busy && latestTask && <TextTaskProgress task={latestTask} />}
    </>
  );
}
