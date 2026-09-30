import { useTranslation } from "react-i18next";
import { AlertTriangle, Bot, CheckCircle2, Loader2, OctagonAlert, RotateCcw, Save } from "lucide-react";
import type { DraftDocType, DraftSoftViolation, ScriptReviewViolation } from "@/types";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { CARD_STYLE, GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";
import { itemIdsInEpisodeText } from "@/utils/episode-display";

/** 把一段指令预填进 Agent 输入框并打开面板；只填不发送，用户核对后自行发送。 */
export function prefillAssistant(text: string): void {
  useAssistantStore.getState().setInput(text);
  useAppStore.getState().setAssistantPanelOpen(true);
}

/**
 * 待修复草稿交给 Agent 时的预填：列出违约，或在违约已清零时请它直接采用。
 * `episodeRef` 是集名连同集 ID 的指称（`episodeAgentRef`）。
 */
export function draftFixRequestText(
  t: (key: string, options?: Record<string, unknown>) => string,
  episodeRef: string,
  docType: DraftDocType,
  violations: ScriptReviewViolation[],
): string {
  const draftName = t(
    docType === "reference_prompt_authoring" ? "dashboard:draft_name_prompt_authoring" : "dashboard:draft_name_script_plan",
  );
  if (violations.length === 0) {
    return t("dashboard:draft_fix_request_promote_prefill", { episodeRef, docType, draftName });
  }
  return [
    t("dashboard:draft_fix_request_prefill_header", { episodeRef, count: violations.length, docType, draftName }),
    ...violations.map((v, i) => `${i + 1}. ${v.message}`),
  ].join("\n");
}

export interface DraftItemJump {
  index: number;
  label: string;
  count: number;
}

interface InvalidDraftBarProps {
  /** 标题；缺省为「待修复草稿」。 */
  title?: string;
  violationCount: number;
  itemJumps: DraftItemJump[];
  episodeLevelCount: number;
  onJump: (index: number) => void;
  onJumpEpisodeLevel: () => void;
  /** 草稿能否在页面上修改（结构收不成可编辑形状时为 false）。 */
  editable: boolean;
  dirty: boolean;
  saving: boolean;
  busy: boolean;
  outdated: boolean;
  onSave: () => void;
  onReloadLatest: () => void;
  onHandToAgent: () => void;
  onDiscard: () => void;
}

/**
 * 待修复草稿的状态条：违约计数与逐条目跳转、整集层面的违约计数，以及「交给 Agent / 保存并校验 /
 * 丢弃草稿」三个入口。违约清零时保存即可采用，此时即便没有改动也允许保存。
 */
export function InvalidDraftBar({
  title,
  violationCount,
  itemJumps,
  episodeLevelCount,
  onJump,
  onJumpEpisodeLevel,
  editable,
  dirty,
  saving,
  busy,
  outdated,
  onSave,
  onReloadLatest,
  onHandToAgent,
  onDiscard,
}: InvalidDraftBarProps) {
  const { t } = useTranslation("dashboard");
  const clean = violationCount === 0;
  const canSave = editable && !busy && (dirty || clean);
  return (
    <header
      className="sticky top-0 z-10 flex flex-col gap-2 rounded-[10px] border border-red-500/35 px-3.5 py-2.5 backdrop-blur-md"
      style={CARD_STYLE}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2">
          {clean ? (
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" aria-hidden="true" />
          ) : (
            <OctagonAlert className="mt-0.5 h-4 w-4 shrink-0 text-red-400" aria-hidden="true" />
          )}
          <div className="flex min-w-0 flex-col">
            <span className="text-[12.5px] font-medium text-text">
              {title ?? t("draft_status_invalid")}
              {!clean && <span className="ml-1.5 text-red-300">{t("draft_violation_count", { count: violationCount })}</span>}
            </span>
            <span className="text-[11px] text-text-4">
              {clean ? (
                t("draft_no_violations_hint")
              ) : (
                <>
                  {[
                    ...(episodeLevelCount > 0
                      ? [{ key: "episode", label: t("draft_episode_level_count", { count: episodeLevelCount }), onClick: onJumpEpisodeLevel }]
                      : []),
                    ...itemJumps.map((jump) => ({
                      key: `item-${String(jump.index)}`,
                      label: `${jump.label} · ${String(jump.count)}`,
                      onClick: () => onJump(jump.index),
                    })),
                  ].map((jump, i) => (
                    <span key={jump.key}>
                      {i > 0 && ", "}
                      <button
                        type="button"
                        onClick={jump.onClick}
                        className="text-red-300 underline decoration-red-300/40 underline-offset-2 hover:decoration-red-300"
                      >
                        {jump.label}
                      </button>
                    </span>
                  ))}
                  <span> — {editable ? t("draft_edit_hint") : t("draft_unrenderable")}</span>
                </>
              )}
            </span>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={onHandToAgent}
            disabled={busy || dirty}
            title={dirty ? t("draft_hand_to_agent_dirty_hint") : undefined}
            className={GHOST_BTN_CLS}
          >
            <Bot className="h-3.5 w-3.5" aria-hidden="true" />
            {t("draft_hand_to_agent")}
          </button>
          {editable && (
            <PrimaryButton
              size="sm"
              onClick={onSave}
              disabled={!canSave}
              leadingIcon={
                saving ? (
                  <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden="true" />
                ) : (
                  <Save className="h-3.5 w-3.5" aria-hidden="true" />
                )
              }
            >
              {saving ? t("draft_saving") : t("draft_save_action")}
            </PrimaryButton>
          )}
        </div>
      </div>
      <div className="flex items-center justify-between gap-3">
        {outdated ? (
          <span className="flex items-center gap-1.5 text-[11px] text-amber-300">
            <AlertTriangle className="h-3 w-3" aria-hidden="true" />
            {t("draft_outdated_hint")}
            <button
              type="button"
              onClick={onReloadLatest}
              className="inline-flex items-center gap-1 underline decoration-amber-300/40 underline-offset-2 hover:decoration-amber-300"
            >
              <RotateCcw className="h-3 w-3" aria-hidden="true" />
              {t("draft_reload_latest")}
            </button>
          </span>
        ) : (
          <span />
        )}
        <button
          type="button"
          onClick={onDiscard}
          disabled={busy}
          className="text-[11px] text-red-300 underline decoration-red-300/40 underline-offset-2 hover:decoration-red-300 disabled:opacity-50"
        >
          {t("draft_discard_action")}
        </button>
      </div>
    </header>
  );
}

interface AgentDraftBarProps {
  busy: boolean;
  onFinish: () => void;
  onDiscard: () => void;
}

/** Agent 的可编辑草稿在场：不展示内容，只给「交给 Agent 完成 / 丢弃这份修改」两个入口。 */
export function AgentDraftBar({ busy, onFinish, onDiscard }: AgentDraftBarProps) {
  const { t } = useTranslation("dashboard");
  return (
    <header
      className="sticky top-0 z-10 flex items-center justify-between gap-3 rounded-[10px] border border-amber-500/35 px-3.5 py-2.5 backdrop-blur-md"
      style={CARD_STYLE}
    >
      <div className="flex min-w-0 items-start gap-2">
        <Bot className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" aria-hidden="true" />
        <div className="flex min-w-0 flex-col">
          <span className="text-[12.5px] font-medium text-text">{t("draft_agent_editing_title")}</span>
          <span className="text-[11px] text-text-4">{t("draft_agent_editing_hint")}</span>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        <button
          type="button"
          onClick={onDiscard}
          disabled={busy}
          className="text-[11px] text-red-300 underline decoration-red-300/40 underline-offset-2 hover:decoration-red-300 disabled:opacity-50"
        >
          {t("draft_agent_discard")}
        </button>
        <PrimaryButton size="sm" onClick={onFinish} disabled={busy} leadingIcon={<Bot className="h-3.5 w-3.5" aria-hidden="true" />}>
          {t("draft_agent_finish")}
        </PrimaryButton>
      </div>
    </header>
  );
}

/** 整集层面的违约：置顶呈现，是状态条「整集」跳转的落点。 */
export function DraftEpisodeViolations({
  violations,
  anchorRef,
}: {
  violations: ScriptReviewViolation[];
  anchorRef?: (el: HTMLElement | null) => void;
}) {
  const { t } = useTranslation("dashboard");
  if (violations.length === 0) return null;
  return (
    <section ref={anchorRef} className="rounded-[10px] border border-red-500/45 p-3.5" style={CARD_STYLE}>
      <h3 className="text-[11px] font-medium text-red-300">{t("draft_episode_level_title")}</h3>
      <DraftViolationList violations={violations} />
    </section>
  );
}

/** 逐条违约（红）。 */
export function DraftViolationList({ violations }: { violations: ScriptReviewViolation[] }) {
  if (violations.length === 0) return null;
  return (
    <ul className="mt-1.5 flex flex-col gap-1">
      {violations.map((v, i) => (
        <li key={`${v.code}-${i}`} className="flex items-start gap-1.5 text-[11px] leading-snug text-red-300">
          <OctagonAlert className="mt-px h-3 w-3 shrink-0" aria-hidden="true" />
          <span>{itemIdsInEpisodeText(v.message)}</span>
        </li>
      ))}
    </ul>
  );
}

/** 逐条降级提示（琥珀）：只提示、不阻断。 */
export function DraftSoftViolationList({ softViolations }: { softViolations: DraftSoftViolation[] }) {
  if (softViolations.length === 0) return null;
  return (
    <ul className="mt-1.5 flex flex-col gap-1">
      {softViolations.map((soft, i) => (
        <li key={`${soft.code}-${i}`} className="flex items-start gap-1.5 text-[11px] leading-snug text-amber-300">
          <AlertTriangle className="mt-px h-3 w-3 shrink-0" aria-hidden="true" />
          <span>{itemIdsInEpisodeText(soft.message)}</span>
        </li>
      ))}
    </ul>
  );
}

interface DiscardDraftDialogProps {
  open: boolean;
  /** Agent 的可编辑草稿用「丢弃这份修改」的措辞。 */
  agentOwned: boolean;
  /** 丢弃后回到哪份内容的说明。 */
  fallbackText: string;
  loading: boolean;
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
}

/** 丢弃草稿的确认：写明丢弃后回到哪份内容。 */
export function DiscardDraftDialog({ open, agentOwned, fallbackText, loading, onConfirm, onCancel }: DiscardDraftDialogProps) {
  const { t } = useTranslation("dashboard");
  return (
    <ConfirmDialog
      open={open}
      tone="danger"
      title={agentOwned ? t("draft_agent_discard_title") : t("draft_discard_title")}
      description={fallbackText}
      confirmLabel={agentOwned ? t("draft_agent_discard") : t("draft_discard_action")}
      loading={loading}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}

/** 丢弃后回到哪份内容：按草稿对应的文档与正式内容是否存在给出说明。 */
export function draftFallbackText(
  t: (key: string, options?: Record<string, unknown>) => string,
  docType: DraftDocType,
  formalExists: boolean,
): string {
  if (docType === "reference_prompt_authoring") return t("dashboard:draft_discard_to_formal_script");
  return formalExists
    ? t("dashboard:draft_discard_to_formal_script_plan")
    : t("dashboard:draft_discard_to_empty_script_plan");
}
