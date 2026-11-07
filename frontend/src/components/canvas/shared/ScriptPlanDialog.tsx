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
  /** Last saved additional instructions for this episode. */
  savedInstructions?: string;
}

/** Single episode-page script-replanning dialog host, opened through {@link useScriptPlanStore} requests. */
export function ScriptPlanHost({ projectName, episode, savedInstructions }: HostProps) {
  const request = useScriptPlanStore((s) => s.request);
  const close = useScriptPlanStore((s) => s.close);
  useEffect(() => close, [close, projectName, episode]);
  if (!request || request.projectName !== projectName || request.episode !== episode) return null;
  return (
    <ScriptPlanDialog
      // Reinitialize each opening from the request and saved instructions, never previous unsubmitted dialog input.
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
 * Replan script replaces current episode planning, repairable drafts or formal scripts. Initial planning uses the episode start area;
 * both share {@link useScriptPlanSubmit}. Ignore close requests during submission.
 */
function ScriptPlanDialog({ request, savedInstructions, onClose }: DialogProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const fieldId = useId();
  const { projectName, episode, replaces } = request;
  const regenerate = replaces !== "formal_script";
  // Unconfirmed plans/repairable drafts have no version history and are lost on whole replacement; the dialog itself confirms replacement first.
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
