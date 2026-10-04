import { useId } from "react";
import { Bot } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import type { StepAct } from "./step-list";

interface Props {
  act: StepAct;
  onRun: (act: StepAct) => void;
  /** 在「或者 …」、行内常驻入口与提醒里一律降为文字链，只有下一步的主入口保留按钮形态。 */
  asLink?: boolean;
  busy?: boolean;
}

/**
 * 下一步入口的统一形态，集页面板与顶栏弹层共用。主入口：「交给 Agent」实心、直接 AI 调用描边、
 * 跳转与手动为幽灵按钮；降为文字链时（`asLink`）一律是链接样式，破坏性操作始终是危险色。
 * 准入不满足时照常显示但不可点，悬停与读屏都给出原因。
 */
export function StepActButton({ act, onRun, asLink = false, busy = false }: Props) {
  const reasonId = useId();
  const disabled = Boolean(act.disabledReason) || busy;
  const variant =
    act.kind === "danger"
      ? "destructive"
      : asLink
        ? "link"
        : act.kind === "agent"
          ? "default"
          : act.kind === "ai"
            ? "outline"
            : "ghost";
  return (
    // 置灰的入口保留聚焦而不用原生 disabled，按钮自带的禁用淡化不生效，由外层淡化表明不可点
    <span className={cn("inline-flex", disabled && "cursor-not-allowed opacity-50")}>
      <Button
        variant={variant}
        size={asLink ? "xs" : "sm"}
        // 用 aria-disabled 而非 disabled：置灰的入口仍要能聚焦，读屏才读得到置灰原因。
        aria-disabled={disabled || undefined}
        aria-describedby={act.disabledReason ? reasonId : undefined}
        title={act.disabledReason ?? undefined}
        onClick={() => {
          if (!disabled) onRun(act);
        }}
      >
        {act.kind === "agent" && !asLink && <Bot aria-hidden data-icon="inline-start" />}
        {act.label}
      </Button>
      {act.disabledReason && (
        <span id={reasonId} className="sr-only">
          {act.disabledReason}
        </span>
      )}
    </span>
  );
}
