import { useTranslation } from "react-i18next";
import { Loader2, RotateCcw, Sparkles } from "lucide-react";
import { useScriptPlanEntry } from "@/hooks/useScriptPlanEntry";
import { useScriptPlanStore, type ScriptPlanReplacement } from "@/stores/script-plan-store";

interface Props {
  projectName: string;
  episode: number;
  replaces: ScriptPlanReplacement;
  className?: string;
  /** 额外的禁用条件（如页面上有未保存的编辑）与对应的悬停说明。 */
  disabledReason?: string | null;
}

/**
 * 打开「AI 规划脚本」弹窗的入口。本集还没有规划时文字为「AI 规划脚本」，否则为「重新规划」。
 * 规划在跑或准入不成立时照常显示、置灰，悬停说明原因。
 */
export function ScriptPlanButton({ projectName, episode, replaces, className = "", disabledReason }: Props) {
  const { t } = useTranslation("dashboard");
  const open = useScriptPlanStore((s) => s.open);
  const { busy, refusedReason } = useScriptPlanEntry(projectName, episode);
  const reason = busy ? t("script_plan_busy") : (refusedReason ?? disabledReason ?? null);
  const Icon = busy ? Loader2 : replaces === "none" ? Sparkles : RotateCcw;
  return (
    <button
      type="button"
      className={`inline-flex items-center gap-1.5 disabled:cursor-not-allowed disabled:opacity-50 ${className}`.trim()}
      disabled={reason !== null}
      title={reason ?? undefined}
      onClick={() => open({ projectName, episode, replaces })}
    >
      <Icon className={`h-3.5 w-3.5${busy ? " motion-safe:animate-spin" : ""}`} aria-hidden="true" />
      <span>{replaces === "none" ? t("script_plan_open") : t("script_plan_regenerate_open")}</span>
    </button>
  );
}
