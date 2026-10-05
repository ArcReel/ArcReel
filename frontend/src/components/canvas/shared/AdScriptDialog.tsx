import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "wouter";
import { cn } from "cn";
import { Bot, CheckCircle2, Loader2, RotateCcw, Sparkles, X } from "lucide-react";
import { ApiRequestError } from "@/api";
import { enqueueAdScript, promptAuthoringResourceId } from "@/actions/generation";
import { ScriptOverwriteConfirmDialog } from "@/components/shared/ScriptOverwriteConfirmDialog";
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
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { isAdScriptTask, useAdScriptEntry } from "@/hooks/useAdScriptEntry";
import { useEpisodeLedger } from "@/hooks/useEpisodeLedger";
import { useAdScriptStore, type AdScriptOpenRequest } from "@/stores/ad-script-store";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { isResourceBusy } from "@/stores/tasks-store";
import type { AdScriptTaskResult, ScriptOverwrite } from "@/types";
import { errMsg } from "@/utils/async";
import { episodeAgentRef } from "@/utils/episode-display";
import { formatNameList } from "@/utils/list-format";

function readScriptOverwrite(err: unknown): ScriptOverwrite | null {
  if (!(err instanceof ApiRequestError) || err.status !== 409) return null;
  const diagnostic = err.diagnostic as { script_overwrite?: ScriptOverwrite } | undefined;
  return diagnostic?.script_overwrite ?? null;
}

/** 集页上唯一的广告/短片「AI 生成脚本」弹窗宿主：按 {@link useAdScriptStore} 的请求打开。 */
export function AdScriptHost({ projectName, episode }: { projectName: string; episode: number }) {
  const request = useAdScriptStore((s) => s.request);
  const close = useAdScriptStore((s) => s.close);
  useEffect(() => close, [close, projectName, episode]);
  if (!request || request.projectName !== projectName || request.episode !== episode) return null;
  return (
    <AdScriptDialog
      // 每次打开都重新初始化，不沿用上一次弹窗里未提交的输入。
      key={`${projectName}:${episode}:${request.regenerate}`}
      request={request}
      onClose={close}
    />
  );
}

interface DialogProps {
  request: AdScriptOpenRequest;
  onClose: () => void;
}

/**
 * 广告/短片整份生成的弹窗：「AI 生成脚本」直接提交，结果写成正式脚本；「交给 Agent」把同一请求预填给助手。
 * 整份重做时服务端先拒绝并交回丢失清单，确认后带上清单版本重新提交。附加指令只随本次提交，不保存。
 */
export function AdScriptDialog({ request, onClose }: DialogProps) {
  const { t } = useTranslation(["dashboard", "workflow"]);
  const episodeLedger = useEpisodeLedger();
  const fieldId = useId();
  const { projectName, episode, regenerate } = request;
  const [instructions, setInstructions] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [overwrite, setOverwrite] = useState<ScriptOverwrite | null>(null);

  const submit = async (overwriteRevision: string | null) => {
    if (submitting) return;
    if (isResourceBusy("text_episode_script", projectName, promptAuthoringResourceId(episode))) {
      useAppStore.getState().pushToast(t("ad_script_busy"), "error");
      return;
    }
    setSubmitting(true);
    try {
      await enqueueAdScript(projectName, episode, {
        instructions: instructions.trim() || null,
        regenerate,
        overwrite_revision: overwriteRevision,
      });
      setOverwrite(null);
      onClose();
    } catch (err) {
      const loss = readScriptOverwrite(err);
      setOverwrite(loss);
      if (!loss) useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setSubmitting(false);
    }
  };

  const handOff = () => {
    const episodeRef = episodeAgentRef(episodeLedger, episode, t);
    const lines = [
      regenerate
        ? t("ad_script_agent_prefill_regenerate", { episodeRef })
        : t("workflow:agent_prefill_generate_script", { episodeRef }),
    ];
    const extra = instructions.trim();
    if (extra) lines.push(t("workflow:agent_prefill_instructions", { instructions: extra }));
    useAssistantStore.getState().setInput(lines.join("\n"));
    useAppStore.getState().setAssistantPanelOpen(true);
    onClose();
  };

  const actionLabel = regenerate ? t("ad_script_regenerate") : t("ad_script_generate");

  return (
    <>
      <Dialog
        open={overwrite === null}
        onOpenChange={(next) => {
          if (!next && !submitting) onClose();
        }}
      >
        <DialogContent showCloseButton={!submitting}>
          <DialogHeader>
            <DialogTitle>{actionLabel}</DialogTitle>
            <DialogDescription>{regenerate ? t("ad_script_regenerate_desc") : t("ad_script_desc")}</DialogDescription>
          </DialogHeader>
          <DialogBody>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={fieldId}>{t("ad_script_instructions_label")}</Label>
              <Textarea
                id={fieldId}
                value={instructions}
                onChange={(event) => setInstructions(event.target.value)}
                maxLength={4000}
                placeholder={t("ad_script_instructions_placeholder")}
                disabled={submitting}
              />
            </div>
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" onClick={() => void submit(null)} disabled={submitting}>
              <Sparkles aria-hidden data-icon="inline-start" />
              {actionLabel}
            </Button>
            <Button onClick={handOff} disabled={submitting}>
              <Bot aria-hidden data-icon="inline-start" />
              {t("script_plan_hand_to_agent")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {overwrite && (
        <ScriptOverwriteConfirmDialog
          open
          overwrite={overwrite}
          loading={submitting}
          title={t("ad_script_overwrite_title")}
          confirmLabel={t("ad_script_overwrite_confirm")}
          loadingLabel={t("ad_script_overwrite_confirm")}
          onConfirm={() => void submit(overwrite.revision)}
          onCancel={() => setOverwrite(null)}
        />
      )}
    </>
  );
}

interface ButtonProps {
  projectName: string;
  episode: number;
  regenerate: boolean;
  /** 页头动作用描边小按钮；空状态里的主操作用实心按钮。 */
  prominent?: boolean;
}

/**
 * 打开广告/短片「AI 生成脚本」弹窗的入口；`regenerate` 时为「重新生成脚本」。
 * 任务在跑或缺创作灵感与商品时照常显示、置灰，悬停说明原因。
 */
export function AdScriptButton({ projectName, episode, regenerate, prominent = false }: ButtonProps) {
  const { t } = useTranslation("dashboard");
  const open = useAdScriptStore((s) => s.open);
  const { busy, refusedReason } = useAdScriptEntry(projectName, episode);
  const reasonId = useId();
  const reason = busy ? t("ad_script_busy") : refusedReason;
  const Icon = busy ? Loader2 : regenerate ? RotateCcw : Sparkles;
  const button = (
    <Button
      variant={prominent ? "default" : "outline"}
      size={prominent ? "default" : "sm"}
      disabled={reason !== null}
      aria-describedby={reason !== null ? reasonId : undefined}
      onClick={() => open({ projectName, episode, regenerate })}
    >
      <Icon className={busy ? "animate-spin" : undefined} aria-hidden data-icon="inline-start" />
      {regenerate ? t("ad_script_regenerate") : t("ad_script_generate")}
    </Button>
  );
  if (reason === null) return button;
  // 置灰的按钮收不到悬停：提示挂在外层 span 上，原因另以 sr-only 文本作按钮的描述
  return (
    <Tooltip>
      <TooltipTrigger render={<span className="inline-flex" />}>
        {button}
        <span id={reasonId} className="sr-only">
          {reason}
        </span>
      </TooltipTrigger>
      <TooltipContent>{reason}</TooltipContent>
    </Tooltip>
  );
}

interface ProgressProps {
  projectName: string;
  episode: number;
  /**
   * 本集还没有正式脚本：这时本集的脚本文本任务只可能是整份生成，排队与生成中都呈现。
   * 有正式脚本时只呈现整份生成任务（与提示词编写共用占用槽），并在完成后列出新登记的资产。
   */
  noScript: boolean;
  className?: string;
}

const STATUS_CLS = "flex gap-2.5 rounded-xl border border-primary/20 bg-primary/10 px-4 py-3 text-sm text-subtle-foreground";

/**
 * 广告/短片整份生成的任务进度：排队 / 生成中，或本次登记的待生成资产。
 * 失败的原因与出路由集页顶部的 `TextTaskFailureNote` 统一呈现，这里不重复。
 */
export function AdScriptProgress({ projectName, episode, noScript, className = "" }: ProgressProps) {
  const { t, i18n } = useTranslation("dashboard");
  const { busy, latestTask } = useAdScriptEntry(projectName, episode);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const relevant = noScript || isAdScriptTask(latestTask);
  if (!latestTask || !relevant || dismissed === latestTask.task_id) return null;

  if (busy) {
    return (
      <div
        role="status"
        className={cn(STATUS_CLS, "items-center", className)}
      >
        <Loader2 className="size-4 shrink-0 animate-spin text-primary" aria-hidden />
        <span>
          {latestTask.status === "running" ? t("ad_script_progress_running") : t("ad_script_progress_queued")}{" "}
          <span className="text-muted-foreground">{t("ad_script_progress_hint")}</span>
        </span>
      </div>
    );
  }

  const dismiss = (
    <Button
      variant="ghost"
      size="icon-xs"
      onClick={() => setDismissed(latestTask.task_id)}
      aria-label={t("ad_script_dismiss")}
      className="ml-auto"
    >
      <X aria-hidden="true" />
    </Button>
  );

  const registered = (latestTask.result as AdScriptTaskResult | null)?.new_assets ?? [];
  if (latestTask.status !== "succeeded" || registered.length === 0) return null;
  return (
    <div
      role="status"
      className={cn(STATUS_CLS, "items-start", className)}
    >
      <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />
      <span>
        {t("ad_script_new_assets", {
          count: registered.length,
          names: formatNameList(
            registered.map((asset) => asset.name),
            i18n.language,
          ),
        })}
      </span>
      {dismiss}
    </div>
  );
}

/** 广告/短片在没有正式脚本时的置灰说明附带的去处：创作灵感在项目概览，商品在商品页，都从概览进入。 */
export function AdScriptInputsLink({ className = "" }: { className?: string }) {
  const { t } = useTranslation("dashboard");
  return (
    <Link href="/" className={cn("rounded-sm underline underline-offset-2 focus-ring", className)}>
      {t("ad_script_edit_inputs")}
    </Link>
  );
}
