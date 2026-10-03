import { useEffect, useId, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "wouter";
import { Bot, CheckCircle2, Loader2, RotateCcw, Sparkles, X } from "lucide-react";
import { ApiRequestError } from "@/api";
import { enqueueAdScript, promptAuthoringResourceId } from "@/actions/generation";
import { ScriptOverwriteConfirmDialog } from "@/components/shared/ScriptOverwriteConfirmDialog";
import { GlassModal } from "@/components/legacy/GlassModal";
import { PrimaryButton } from "@/components/legacy/PrimaryButton";
import { SecondaryButton } from "@/components/legacy/SecondaryButton";
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

const FIELD_STYLE: CSSProperties = {
  background: "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.6), oklch(0.18 0.010 265 / 0.45))",
  border: "1px solid var(--color-hairline)",
  color: "var(--color-text)",
  boxShadow: "inset 0 1px 2px oklch(0 0 0 / 0.2)",
};

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
  const titleId = useId();
  const descId = useId();
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
      <GlassModal
        open={overwrite === null}
        onClose={() => {
          if (!submitting) onClose();
        }}
        labelledBy={titleId}
        describedBy={descId}
        widthClassName="w-full max-w-lg"
        closeOnBackdrop={!submitting}
        closeOnEscape={!submitting}
      >
        <div className="p-5">
          <h2
            id={titleId}
            className="display-serif text-[17px] font-semibold tracking-tight"
            style={{ color: "var(--color-text)" }}
          >
            {actionLabel}
          </h2>
          <p id={descId} className="mt-1.5 text-[12.5px] leading-[1.55]" style={{ color: "var(--color-text-3)" }}>
            {regenerate ? t("ad_script_regenerate_desc") : t("ad_script_desc")}
          </p>

          <label
            htmlFor={fieldId}
            className="mt-4 block text-[12px] font-medium"
            style={{ color: "var(--color-text-2)" }}
          >
            {t("ad_script_instructions_label")}
          </label>
          <textarea
            id={fieldId}
            value={instructions}
            onChange={(event) => setInstructions(event.target.value)}
            rows={3}
            maxLength={4000}
            placeholder={t("ad_script_instructions_placeholder")}
            className="focus-ring mt-1.5 w-full resize-none rounded-lg px-3 py-2 text-[13px] leading-[1.55] outline-none"
            style={FIELD_STYLE}
          />

          <div className="mt-4 flex items-center justify-end gap-2">
            <SecondaryButton
              size="sm"
              onClick={() => void submit(null)}
              disabled={submitting}
              leadingIcon={<Sparkles className="h-3.5 w-3.5" aria-hidden="true" />}
            >
              {actionLabel}
            </SecondaryButton>
            <PrimaryButton
              size="sm"
              onClick={handOff}
              disabled={submitting}
              leadingIcon={<Bot className="h-3.5 w-3.5" aria-hidden="true" />}
            >
              {t("script_plan_hand_to_agent")}
            </PrimaryButton>
          </div>
        </div>
      </GlassModal>

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
  className?: string;
}

/**
 * 打开广告/短片「AI 生成脚本」弹窗的入口；`regenerate` 时为「重新生成脚本」。
 * 任务在跑或缺创作灵感与商品时照常显示、置灰，悬停说明原因。
 */
export function AdScriptButton({ projectName, episode, regenerate, className = "" }: ButtonProps) {
  const { t } = useTranslation("dashboard");
  const open = useAdScriptStore((s) => s.open);
  const { busy, refusedReason } = useAdScriptEntry(projectName, episode);
  const reason = busy ? t("ad_script_busy") : refusedReason;
  const Icon = busy ? Loader2 : regenerate ? RotateCcw : Sparkles;
  return (
    <button
      type="button"
      className={`inline-flex items-center gap-1.5 disabled:cursor-not-allowed disabled:opacity-50 ${className}`.trim()}
      disabled={reason !== null}
      title={reason ?? undefined}
      onClick={() => open({ projectName, episode, regenerate })}
    >
      <Icon className={`h-3.5 w-3.5${busy ? " motion-safe:animate-spin" : ""}`} aria-hidden="true" />
      <span>{regenerate ? t("ad_script_regenerate") : t("ad_script_generate")}</span>
    </button>
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
        className={`flex items-center gap-2.5 rounded-xl px-4 py-3 text-[12.5px] ${className}`.trim()}
        style={{ background: "var(--color-accent-dim)", border: "1px solid var(--color-accent-soft)", color: "var(--color-text-2)" }}
      >
        <Loader2 className="h-4 w-4 shrink-0 motion-safe:animate-spin" style={{ color: "var(--color-accent-2)" }} aria-hidden />
        <span>
          {latestTask.status === "running" ? t("ad_script_progress_running") : t("ad_script_progress_queued")}{" "}
          <span style={{ color: "var(--color-text-4)" }}>{t("ad_script_progress_hint")}</span>
        </span>
      </div>
    );
  }

  const dismiss = (
    <button
      type="button"
      onClick={() => setDismissed(latestTask.task_id)}
      aria-label={t("ad_script_dismiss")}
      title={t("ad_script_dismiss")}
      className="focus-ring ml-auto shrink-0 rounded p-0.5 opacity-70 transition-opacity hover:opacity-100"
    >
      <X className="h-3.5 w-3.5" aria-hidden="true" />
    </button>
  );

  const registered = (latestTask.result as AdScriptTaskResult | null)?.new_assets ?? [];
  if (latestTask.status !== "succeeded" || registered.length === 0) return null;
  return (
    <div
      role="status"
      className={`flex items-start gap-2.5 rounded-xl px-4 py-3 text-[12.5px] ${className}`.trim()}
      style={{ background: "var(--color-accent-dim)", border: "1px solid var(--color-accent-soft)", color: "var(--color-text-2)" }}
    >
      <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" style={{ color: "var(--color-accent-2)" }} aria-hidden />
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
    <Link href="/" className={`focus-ring underline underline-offset-2 ${className}`.trim()}>
      {t("ad_script_edit_inputs")}
    </Link>
  );
}
