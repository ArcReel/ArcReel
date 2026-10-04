import { Fragment } from "react";
import { Check } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

export type WizardStep = 1 | 2 | 3;

const STEP_LABEL_KEYS: Record<WizardStep, string> = {
  1: "templates:wizard_step_basics",
  2: "templates:wizard_step_generation",
  3: "templates:wizard_step_style",
};

const STEPS: readonly WizardStep[] = [1, 2, 3];

/** 横向步骤条：当前步骤高亮，已完成的步骤显示对勾。只做指示，不可点击跳转。 */
export function WizardStepper({ current }: { current: WizardStep }) {
  const { t } = useTranslation(["dashboard", "templates"]);
  return (
    <ol aria-label={t("dashboard:wizard_steps_label")} className="flex items-center gap-2">
      {STEPS.map((step) => {
        const done = step < current;
        const active = step === current;
        return (
          <Fragment key={step}>
            {step > 1 && <li aria-hidden className="h-px w-8 bg-border" />}
            <li aria-current={active ? "step" : undefined} className="flex items-center gap-2">
              <span
                className={cn(
                  "grid size-5 place-items-center rounded-full text-xs tabular-nums",
                  active
                    ? "bg-primary text-primary-foreground"
                    : done
                      ? "bg-primary/20 text-primary"
                      : "bg-muted text-muted-foreground",
                )}
              >
                {done ? <Check className="size-3" aria-hidden /> : step}
              </span>
              <span className={cn("text-sm", active ? "font-medium text-foreground" : "text-muted-foreground")}>
                {t(STEP_LABEL_KEYS[step])}
                {done && <span className="sr-only">{t("dashboard:wizard_step_done")}</span>}
              </span>
            </li>
          </Fragment>
        );
      })}
    </ol>
  );
}
