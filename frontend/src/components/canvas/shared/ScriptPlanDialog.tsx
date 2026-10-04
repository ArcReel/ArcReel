import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Bot, Sparkles } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useScriptPlanSubmit } from "@/hooks/useScriptPlanSubmit";
import { useScriptPlanStore, type ScriptPlanOpenRequest } from "@/stores/script-plan-store";

interface HostProps {
  projectName: string;
  episode: number;
  /** 本集上次保存的附加指令。 */
  savedInstructions?: string;
}

/** 集页上唯一的「重新规划脚本」对话框宿主：按 {@link useScriptPlanStore} 的请求打开。 */
export function ScriptPlanHost({ projectName, episode, savedInstructions }: HostProps) {
  const request = useScriptPlanStore((s) => s.request);
  const close = useScriptPlanStore((s) => s.close);
  useEffect(() => close, [close, projectName, episode]);
  if (!request || request.projectName !== projectName || request.episode !== episode) return null;
  return (
    <ScriptPlanDialog
      // 每次打开都从请求与已保存的指令重新初始化，不沿用上一次对话框里未提交的输入。
      key={`${projectName}:${episode}:${request.replaces}`}
      request={request}
      savedInstructions={savedInstructions ?? ""}
      onClose={close}
    />
  );
}

interface DialogProps {
  request: ScriptPlanOpenRequest;
  savedInstructions: string;
  onClose: () => void;
}

/**
 * 「重新规划脚本」：替换本集已有的规划、待修复草稿或正式脚本。首次规划在集页起步区里完成，
 * 两处共用 {@link useScriptPlanSubmit} 的提交逻辑。提交中忽略关闭请求。
 */
function ScriptPlanDialog({ request, savedInstructions, onClose }: DialogProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const fieldId = useId();
  const { projectName, episode, replaces } = request;
  const regenerate = replaces !== "none" && replaces !== "formal_script";
  // 未确认的规划与待修复草稿没有版本历史，整份替换即丢失，对话框本身就是替换前的确认。
  const lossText =
    replaces === "pending_plan"
      ? t("script_plan_replace_pending_warning")
      : replaces === "draft"
        ? t("script_plan_replace_draft_warning")
        : null;
  const [instructions, setInstructions] = useState(savedInstructions);
  const { submitting, submit, handOff } = useScriptPlanSubmit(projectName, episode, lossText !== null);

  const run = async (action: (value: string) => Promise<boolean>) => {
    if (await action(instructions)) onClose();
  };

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !submitting) onClose();
      }}
    >
      <DialogContent showCloseButton={!submitting}>
        <DialogHeader>
          <DialogTitle>{regenerate ? t("script_plan_regenerate_title") : t("script_plan_title")}</DialogTitle>
          <DialogDescription>{t("script_plan_desc")}</DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="flex flex-col gap-4">
            {(replaces === "confirmed_plan" || replaces === "formal_script") && (
              <p className="text-sm text-muted-foreground">{t("script_plan_replace_confirmed_hint")}</p>
            )}
            {lossText && (
              <Alert variant="destructive">
                <AlertTriangle aria-hidden />
                <AlertDescription>{lossText}</AlertDescription>
              </Alert>
            )}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={fieldId}>{t("script_plan_instructions_label")}</Label>
              <Textarea
                id={fieldId}
                value={instructions}
                onChange={(event) => setInstructions(event.target.value)}
                maxLength={4000}
                placeholder={t("script_plan_instructions_placeholder")}
                disabled={submitting}
              />
            </div>
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" disabled={submitting} onClick={() => void run(submit)}>
            <Sparkles aria-hidden data-icon="inline-start" />
            {regenerate ? t("script_plan_ai_regenerate") : t("script_plan_ai_plan")}
          </Button>
          <Button disabled={submitting} onClick={() => void run(handOff)}>
            <Bot aria-hidden data-icon="inline-start" />
            {t("script_plan_hand_to_agent")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
