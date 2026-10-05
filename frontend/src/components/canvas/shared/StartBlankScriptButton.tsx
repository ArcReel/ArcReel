import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FilePlus2, Loader2 } from "lucide-react";
import { cn } from "cn";
import { API } from "@/api";
import { AdScriptButton, AdScriptInputsLink, AdScriptProgress } from "@/components/canvas/shared/AdScriptDialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useAdScriptEntry } from "@/hooks/useAdScriptEntry";
import { useAppStore } from "@/stores/app-store";
import { refreshAfterWrite } from "./refreshAfterWrite";
import { errMsg } from "@/utils/async";

interface Props {
  projectName: string;
  episode: number;
  /** 本集有未确认的脚本规划或待修复草稿：先确认弃置再建正式脚本。 */
  discardsPlan: boolean;
  variant?: "outline" | "ghost";
  /** 只用于布局（如 `ml-auto`）。 */
  className?: string;
}

/**
 * 「从空白开始」：本集没有正式脚本时建出空的正式脚本，之后在时间线上逐条添加分镜。
 * 成功后刷新项目，集页随正式脚本出现切到分镜。会弃置规划时先用 AlertDialog 确认。
 */
export function StartBlankScriptButton({ projectName, episode, discardsPlan, variant = "outline", className }: Props) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const start = async () => {
    if (submitting) return;
    setSubmitting(true);
    try {
      await API.startBlankScript(projectName, episode);
      setConfirmOpen(false);
      await refreshAfterWrite(projectName, t);
    } catch (err) {
      useAppStore.getState().pushToast(t("blank_script_failed", { message: errMsg(err) }), "error");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              variant={variant}
              size="sm"
              className={className}
              disabled={submitting}
              onClick={() => (discardsPlan ? setConfirmOpen(true) : void start())}
            />
          }
        >
          {submitting && !discardsPlan ? (
            <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
          ) : (
            <FilePlus2 aria-hidden data-icon="inline-start" />
          )}
          {t("blank_script_start")}
        </TooltipTrigger>
        <TooltipContent>{t("blank_script_hint")}</TooltipContent>
      </Tooltip>
      {discardsPlan && (
        <AlertDialog
          open={confirmOpen}
          onOpenChange={(next) => {
            if (!next && !submitting) setConfirmOpen(false);
          }}
        >
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{t("blank_script_discard_title")}</AlertDialogTitle>
              <AlertDialogDescription>{t("blank_script_discard_desc")}</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={submitting}>{t("common:cancel")}</AlertDialogCancel>
              <AlertDialogAction variant="destructive" disabled={submitting} onClick={() => void start()}>
                {submitting ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
                {t("blank_script_start")}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      )}
    </>
  );
}

/**
 * 没有脚本规划可走的集（广告/短片）在没有正式脚本时的画布：说明现状，「AI 生成脚本」与「从空白开始」并排。
 * 缺创作灵感与商品时「AI 生成脚本」置灰，并给出去填写的链接。
 */
export function NoScriptBlankState({ projectName, episode, className }: { projectName: string; episode: number; className?: string }) {
  const { t } = useTranslation("dashboard");
  const { refusedReason } = useAdScriptEntry(projectName, episode);
  return (
    <div className={cn("flex flex-col items-center justify-center gap-3 text-muted-foreground", className)}>
      <p>{t("timeline_no_script_blank_hint")}</p>
      <div className="flex flex-wrap items-center justify-center gap-2">
        <AdScriptButton projectName={projectName} episode={episode} regenerate={false} prominent />
        <StartBlankScriptButton projectName={projectName} episode={episode} discardsPlan={false} />
      </div>
      {refusedReason && (
        <p className="text-sm">
          {refusedReason} <AdScriptInputsLink className="text-primary" />
        </p>
      )}
      <AdScriptProgress projectName={projectName} episode={episode} noScript className="w-full max-w-md" />
    </div>
  );
}
