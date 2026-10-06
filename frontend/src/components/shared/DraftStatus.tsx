import { useId, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Bot, CheckCircle2, Loader2, OctagonAlert, RotateCcw, Save, Sparkles } from "lucide-react";
import type { DraftDocType, DraftSoftViolation, ScriptReviewViolation } from "@/types";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogBody,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
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
  repairing: boolean;
  onSave: () => void;
  onReloadLatest: () => void;
  onHandToAgent: () => void;
  /** AI 修复已保存的草稿；返回 `true` 时收起弹窗。 */
  onRepair: (instructions: string) => Promise<boolean>;
  onDiscard: () => void;
  /** 「重新规划」入口；草稿没有对应的重新生成动作时省略。 */
  regenerateAction?: ReactNode;
}

/**
 * 待修复草稿的状态条：违约计数与逐条目跳转、整集层面的违约计数。有违约时修复入口以「交给 Agent」为主、
 * 「AI 修复」为次，另有「保存并校验」与「丢弃草稿」，脚本规划草稿另有「重新规划」。违约清零时保存即可采用，
 * 「保存并校验」成为主按钮、即便没有改动也允许保存，不再提供「AI 修复」。有未保存的修改时，「交给 Agent」与
 * 「AI 修复」都不可用：它们读取的是已保存的草稿，原因写在状态条第二行。
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
  repairing,
  onSave,
  onReloadLatest,
  onHandToAgent,
  onRepair,
  onDiscard,
  regenerateAction,
}: InvalidDraftBarProps) {
  const { t } = useTranslation("dashboard");
  const [repairOpen, setRepairOpen] = useState(false);
  const clean = violationCount === 0;
  const canSave = editable && !busy && (dirty || clean);
  const saveIcon = saving ? (
    <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
  ) : (
    <Save aria-hidden data-icon="inline-start" />
  );
  const saveLabel = saving ? t("draft_saving") : t("draft_save_action");
  const jumps = [
    ...(episodeLevelCount > 0
      ? [{ key: "episode", label: t("draft_episode_level_count", { count: episodeLevelCount }), onClick: onJumpEpisodeLevel }]
      : []),
    ...itemJumps.map((jump) => ({
      key: `item-${String(jump.index)}`,
      label: `${jump.label} · ${String(jump.count)}`,
      onClick: () => onJump(jump.index),
    })),
  ];
  return (
    <header
      className={`sticky top-0 z-sticky flex flex-col gap-2 rounded-lg border bg-card px-3.5 py-2.5 ${
        clean ? "border-border" : "border-destructive/40"
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
        <div className="flex min-w-0 items-start gap-2">
          {clean ? (
            <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-good" aria-hidden="true" />
          ) : (
            <OctagonAlert className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden="true" />
          )}
          <div className="flex min-w-0 flex-col gap-0.5">
            <span className="text-sm font-medium text-foreground">
              {title ?? t("draft_status_invalid")}
              {!clean && <span className="ml-1.5 text-destructive">{t("draft_violation_count", { count: violationCount })}</span>}
            </span>
            <span className="text-xs text-muted-foreground">
              {clean ? (
                t("draft_no_violations_hint")
              ) : (
                <>
                  {jumps.map((jump, i) => (
                    <span key={jump.key}>
                      {i > 0 && ", "}
                      <button
                        type="button"
                        onClick={jump.onClick}
                        className="rounded-sm text-destructive underline decoration-destructive/40 underline-offset-2 focus-ring hover:decoration-destructive"
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
          {editable && !clean && (
            <Button variant="outline" size="sm" onClick={onSave} disabled={!canSave}>
              {saveIcon}
              {saveLabel}
            </Button>
          )}
          {!clean && (
            <Button variant="outline" size="sm" onClick={() => setRepairOpen(true)} disabled={busy || dirty}>
              {repairing ? (
                <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
              ) : (
                <Sparkles aria-hidden data-icon="inline-start" />
              )}
              {repairing ? t("draft_repairing") : t("draft_ai_repair")}
            </Button>
          )}
          <Button variant={clean ? "outline" : "default"} size="sm" onClick={onHandToAgent} disabled={busy || dirty}>
            <Bot aria-hidden data-icon="inline-start" />
            {t("draft_hand_to_agent")}
          </Button>
          {editable && clean && (
            <Button size="sm" onClick={onSave} disabled={!canSave}>
              {saveIcon}
              {saveLabel}
            </Button>
          )}
        </div>
      </div>
      {repairOpen && (
        <DraftRepairDialog
          repairing={repairing}
          onSubmit={async (instructions) => {
            if (await onRepair(instructions)) setRepairOpen(false);
          }}
          onClose={() => setRepairOpen(false)}
        />
      )}
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        {outdated ? (
          <span className="flex items-center gap-1.5 text-xs text-warn">
            <AlertTriangle className="size-3 shrink-0" aria-hidden="true" />
            {t("draft_outdated_hint")}
            <Button variant="link" size="xs" onClick={onReloadLatest}>
              <RotateCcw aria-hidden data-icon="inline-start" />
              {t("draft_reload_latest")}
            </Button>
          </span>
        ) : dirty ? (
          <span className="text-xs text-muted-foreground">{t("draft_fix_dirty_hint")}</span>
        ) : (
          <span />
        )}
        <span className="flex items-center gap-3">
          {regenerateAction}
          <Button variant="destructive" size="xs" onClick={onDiscard} disabled={busy}>
            {t("draft_discard_action")}
          </Button>
        </span>
      </div>
    </header>
  );
}

interface DraftRepairDialogProps {
  repairing: boolean;
  onSubmit: (instructions: string) => Promise<void>;
  onClose: () => void;
}

/** 「AI 修复」弹窗：说明修复范围，可带附加指令（随修复提交，不保存）。提交中忽略关闭请求。 */
function DraftRepairDialog({ repairing, onSubmit, onClose }: DraftRepairDialogProps) {
  const { t } = useTranslation("dashboard");
  const fieldId = useId();
  const [instructions, setInstructions] = useState("");
  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !repairing) onClose();
      }}
    >
      <DialogContent showCloseButton={!repairing}>
        <DialogHeader>
          <DialogTitle>{t("draft_repair_title")}</DialogTitle>
          <DialogDescription>{t("draft_repair_desc")}</DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={fieldId}>{t("draft_repair_instructions_label")}</Label>
            <Textarea
              id={fieldId}
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
              maxLength={4000}
              placeholder={t("draft_repair_instructions_placeholder")}
            />
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={repairing}>
            {t("common:cancel")}
          </Button>
          <Button onClick={() => void onSubmit(instructions)} disabled={repairing}>
            {repairing ? (
              <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
            ) : (
              <Sparkles aria-hidden data-icon="inline-start" />
            )}
            {repairing ? t("draft_repairing") : t("draft_repair_submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
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
    <header className="sticky top-0 z-sticky flex flex-wrap items-center justify-between gap-x-3 gap-y-2 rounded-lg border border-warn/40 bg-card px-3.5 py-2.5">
      <div className="flex min-w-0 items-start gap-2">
        <Bot className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden="true" />
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="text-sm font-medium text-foreground">{t("draft_agent_editing_title")}</span>
          <span className="text-xs text-muted-foreground">{t("draft_agent_editing_hint")}</span>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        <Button variant="destructive" size="xs" onClick={onDiscard} disabled={busy}>
          {t("draft_agent_discard")}
        </Button>
        <Button size="sm" onClick={onFinish} disabled={busy}>
          <Bot aria-hidden data-icon="inline-start" />
          {t("draft_agent_finish")}
        </Button>
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
    <section ref={anchorRef} className="rounded-lg border border-destructive/45 bg-card p-3.5">
      <h3 className="text-xs font-medium text-destructive">{t("draft_episode_level_title")}</h3>
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
        <li key={`${v.code}-${i}`} className="flex items-start gap-1.5 text-xs leading-snug text-destructive">
          <OctagonAlert className="mt-px size-3 shrink-0" aria-hidden="true" />
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
        <li key={`${soft.code}-${i}`} className="flex items-start gap-1.5 text-xs leading-snug text-warn">
          <AlertTriangle className="mt-px size-3 shrink-0" aria-hidden="true" />
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

/** 丢弃草稿的确认：写明丢弃后回到哪份内容。丢弃中忽略关闭请求。 */
export function DiscardDraftDialog({ open, agentOwned, fallbackText, loading, onConfirm, onCancel }: DiscardDraftDialogProps) {
  const { t } = useTranslation("dashboard");
  const confirmLabel = agentOwned ? t("draft_agent_discard") : t("draft_discard_action");
  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !loading) onCancel();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{agentOwned ? t("draft_agent_discard_title") : t("draft_discard_title")}</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody tabIndex={0} role="region" aria-label={agentOwned ? t("draft_agent_discard_title") : t("draft_discard_title")}>
          <AlertDialogDescription>{fallbackText}</AlertDialogDescription>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={loading}>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction variant="destructive" disabled={loading} onClick={() => void onConfirm()}>
            {loading ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
            {confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
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
