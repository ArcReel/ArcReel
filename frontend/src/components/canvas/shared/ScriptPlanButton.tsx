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
  /** 新规划会替换的现有内容。集页的首次规划走起步区（`ScriptPlanStart`），不经这里。 */
  replaces: ScriptPlanReplacement;
  className?: string;
  /** 额外的禁用条件（如页面上有未保存的编辑）与对应的说明。 */
  disabledReason?: string | null;
}

/**
 * 打开「AI 规划脚本」对话框的入口。本集还没有规划时文字为「AI 规划脚本」，否则为「重新规划」。
 * 规划在跑或准入不成立时照常显示、置灰，悬停与读屏都给出原因。
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
    // 置灰时保留聚焦而不用原生 disabled，读屏才读得到置灰原因；按钮自带的禁用淡化不生效，由外层淡化
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
