import { useId } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, RotateCcw, Sparkles } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { useScriptPlanEntry } from "@/hooks/useScriptPlanEntry";
import { useScriptPlanStore, type ScriptPlanReplacement } from "@/stores/script-plan-store";

interface Props {
  projectName: string;
  episode: number;
  /** Existing content the new plan would replace. Episode-page initial planning uses `ScriptPlanStart` instead of this entry. */
  replaces: ScriptPlanReplacement;
  className?: string;
  /** Additional disabling conditions, such as unsaved page edits, with explanations. */
  disabledReason?: string | null;
}

/**
 * Entry for opening AI script planning. Label it AI plan script when this episode has no plan, otherwise Replan.
 * Keep visible but disabled while planning runs or admission fails; provide reasons on hover and to screen readers.
 */
export function ScriptPlanButton({ projectName, episode, replaces, className, disabledReason }: Props) {
  const { t } = useTranslation("dashboard");
  const reasonId = useId();
  const open = useScriptPlanStore((s) => s.open);
  const { busy, refusedReason } = useScriptPlanEntry(projectName, episode);
  const reason = busy ? t("script_plan_busy") : (refusedReason ?? disabledReason ?? null);
  const firstPlan = replaces === "formal_script";
  const Icon = busy ? Loader2 : firstPlan ? Sparkles : RotateCcw;
  return (
    // Preserve focus when disabled instead of native disabled so screen readers can access the reason; built-in disabled dimming does not apply, so dim the wrapper
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
  );
}
