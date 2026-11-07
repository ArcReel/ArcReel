import { useId, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Bot, CheckCircle2, Loader2, OctagonAlert, RotateCcw, Save, Sparkles } from "lucide-react";
import type { DraftSoftViolation, ScriptReviewViolation } from "@/types";
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
import { draftViolationMessage } from "@/utils/draft-violations";

export interface DraftItemJump {
  index: number;
  label: string;
  count: number;
}

interface InvalidDraftBarProps {
  /** Titles; defaults to Draft needing repair. */
  title?: string;
  violationCount: number;
  itemJumps: DraftItemJump[];
  episodeLevelCount: number;
  onJump: (index: number) => void;
  onJumpEpisodeLevel: () => void;
  /** Whether drafts are editable on pages; false if structures cannot form editable shapes. */
  editable: boolean;
  dirty: boolean;
  saving: boolean;
  busy: boolean;
  outdated: boolean;
  repairing: boolean;
  onSave: () => void;
  onReloadLatest: () => void;
  onHandToAgent: () => void;
  /** AI repairs saved drafts; returning `true` closes dialogs. */
  onRepair: (instructions: string) => Promise<boolean>;
  onDiscard: () => void;
  /** Replan entries; omit when drafts lack corresponding regeneration actions. */
  regenerateAction?: ReactNode;
}

/**
 * Repair-draft status bars: violation counts/per-entry links and episode-wide counts. With violations, Hand to Agent is primary,
 * AI repair secondary, alongside Save and validate/Discard draft; script-planning drafts also offer Replan. At zero violations, saving adopts drafts,
 * Save and validate becomes primary and stays available even without edits, and AI repair disappears. Unsaved edits disable both Hand to Agent and
 * AI repair because they read saved drafts; second status-bar lines explain why.
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

/** AI repair dialogs: explain scopes, optionally adding instructions submitted with repairs without persistence. Ignore closing while submitting. */
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

/** Existing editable Agent drafts: hide contents and offer only Hand to Agent to finish/Discard these edits. */
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

/** Episode-wide violations pinned first, targeting status-bar Whole episode links. */
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

/** Individual violations (red). */
export function DraftViolationList({ violations, position }: { violations: ScriptReviewViolation[]; position?: number | null }) {
  const { t } = useTranslation();
  if (violations.length === 0) return null;
  return (
    <ul className="mt-1.5 flex flex-col gap-1">
      {violations.map((v, i) => (
        // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- Read-only validation lists lack unique IDs/state; UI neither reorders nor filters, replacing entire lists with results
        <li key={`${v.code}-${i}`} className="flex items-start gap-1.5 text-xs leading-snug text-destructive">
          <OctagonAlert className="mt-px size-3 shrink-0" aria-hidden="true" />
          <span>{draftViolationMessage(v, t, position)}</span>
        </li>
      ))}
    </ul>
  );
}

/** Individual degradation hints (amber): advisory, never blocking. */
export function DraftSoftViolationList({ softViolations, position }: { softViolations: DraftSoftViolation[]; position?: number | null }) {
  const { t } = useTranslation();
  if (softViolations.length === 0) return null;
  return (
    <ul className="mt-1.5 flex flex-col gap-1">
      {softViolations.map((soft, i) => (
        // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- Read-only validation lists lack unique IDs/state; UI neither reorders nor filters, replacing entire lists with results
        <li key={`${soft.code}-${i}`} className="flex items-start gap-1.5 text-xs leading-snug text-warn">
          <AlertTriangle className="mt-px size-3 shrink-0" aria-hidden="true" />
          <span>{draftViolationMessage(soft, t, position)}</span>
        </li>
      ))}
    </ul>
  );
}

interface DiscardDraftDialogProps {
  open: boolean;
  /** Editable Agent drafts use Discard these edits wording. */
  agentOwned: boolean;
  /** Explanations of contents restored after discarding. */
  fallbackText: string;
  loading: boolean;
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
}

/** Discard-draft confirmations specify restored contents; ignore closing during discard. */
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
