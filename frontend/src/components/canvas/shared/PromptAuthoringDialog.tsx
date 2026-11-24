import { useEffect, useId, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Bot, Sparkles } from "lucide-react";
import { API } from "@/api";
import { usePromptAuthoringSubmit } from "@/hooks/usePromptAuthoringSubmit";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Textarea } from "@/components/ui/textarea";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import {
  usePromptAuthoringStore,
  type PromptAuthoringOpenRequest,
  type PromptAuthoringScope,
} from "@/stores/prompt-authoring-store";
import { errMsg } from "@/utils/async";
import { useEpisodeLedger } from "@/hooks/useEpisodeLedger";
import { episodeAgentRef, episodeItemAgentRef, itemIdsAsPositionsInText } from "@/utils/episode-display";
import { EpisodeItemRef } from "@/components/shared/episode-item/EpisodeItemRef";
import { useProjectsStore } from "@/stores/projects-store";
import { promptAuthoringEntries, type PromptAuthoringEntry } from "./prompt-authoring-entries";
import { promptAuthoringHandoffText } from "./prompt-authoring-handoff";
import { PromptOverwriteDialog } from "./PromptOverwriteDialog";

interface HostProps {
  projectName: string;
  episode: number;
  script: unknown;
  savedInstructions?: string;
}

export function PromptAuthoringHost({ projectName, episode, script, savedInstructions }: HostProps) {
  const request = usePromptAuthoringStore((s) => s.request);
  const close = usePromptAuthoringStore((s) => s.close);
  useEffect(() => close, [close, projectName, episode]);
  const entries = useMemo(() => promptAuthoringEntries(script), [script]);
  const unitMode = Boolean(
    script && typeof script === "object" && Array.isArray((script as Record<string, unknown>).video_units),
  );
  if (!request || request.projectName !== projectName || request.episode !== episode || !script) return null;
  return (
    <PromptAuthoringDialog
      key={`${projectName}:${episode}:${request.scope}:${request.currentEntryId ?? ""}`}
      projectName={projectName}
      episode={episode}
      entries={entries}
      unitMode={unitMode}
      request={request}
      savedInstructions={savedInstructions ?? ""}
      onClose={close}
    />
  );
}

interface DialogProps {
  projectName: string;
  episode: number;
  entries: PromptAuthoringEntry[];
  unitMode: boolean;
  request: PromptAuthoringOpenRequest;
  savedInstructions: string;
  onClose: () => void;
}

export function PromptAuthoringDialog({
  projectName,
  episode,
  entries,
  unitMode,
  request,
  savedInstructions,
  onClose,
}: DialogProps) {
  const { t } = useTranslation("dashboard");
  const scripts = useProjectsStore((s) => s.currentScripts);
  const episodeLedger = useEpisodeLedger();
  const scopeLabelId = useId();
  const rewriteId = useId();
  const fieldId = useId();
  const pendingCount = entries.filter((entry) => entry.pending).length;
  const currentEntryId =
    request.currentEntryId && entries.some((entry) => entry.id === request.currentEntryId)
      ? request.currentEntryId
      : null;
  const initialScope: PromptAuthoringScope =
    request.scope === "current" && !currentEntryId ? "pending" : request.scope;

  const [scope, setScope] = useState<PromptAuthoringScope>(initialScope);
  const [selected, setSelected] = useState<string[]>(() =>
    currentEntryId ? [currentEntryId] : entries.filter((entry) => entry.pending).map((entry) => entry.id),
  );
  const [rewrite, setRewrite] = useState(false);
  const [instructions, setInstructions] = useState(savedInstructions);
  const { busy, submitting: enqueuing, overwrite, enqueue, confirmOverwrite, closeOverwrite } = usePromptAuthoringSubmit(
    projectName,
    episode,
  );
  const [handingOff, setHandingOff] = useState(false);
  const submitting = enqueuing || handingOff;

  const selectedSet = useMemo(() => new Set(selected), [selected]);
  const selectedIds = useMemo(
    () => entries.filter((entry) => selectedSet.has(entry.id)).map((entry) => entry.id),
    [entries, selectedSet],
  );

  const entryIds: string[] | null =
    scope === "current" ? (currentEntryId ? [currentEntryId] : []) : scope === "custom" ? selectedIds : null;
  const scopeEmpty = entryIds === null ? pendingCount === 0 : entryIds.length === 0;
  const scopeLabel =
    scope === "pending"
      ? t("prompt_authoring_scope_pending_prefill", { count: pendingCount })
      : (entryIds ?? []).map((id) => episodeItemAgentRef(id, scripts, t)).join(t("prompt_authoring_id_separator"));

  const submit = async (overwriteRevision: string | null) => {
    if (submitting) return;
    if (overwriteRevision !== null) {
      if (await confirmOverwrite(overwriteRevision)) onClose();
      return;
    }
    if (scopeEmpty) return;
    if (await enqueue({
      entry_ids: entryIds,
      rewrite,
      instructions: instructions.trim() || null,
      overwrite_revision: null,
    })) onClose();
  };

  const handOff = async () => {
    if (submitting || scopeEmpty) return;
    setHandingOff(true);
    try {
      await API.savePromptAuthoringInstructions(projectName, episode, instructions.trim());
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
      return;
    } finally {
      setHandingOff(false);
    }
    useAssistantStore.getState().setInput(
      promptAuthoringHandoffText(t, {
        episodeRef: episodeAgentRef(episodeLedger, episode, t),
        scopeLabel,
        rewrite,
        instructions,
      }),
    );
    useAppStore.getState().setAssistantPanelOpen(true);
    onClose();
  };

  const toggle = (id: string) =>
    setSelected((current) =>
      current.includes(id) ? current.filter((value) => value !== id) : [...current, id],
    );

  const scopes: { value: PromptAuthoringScope; label: string; disabled: boolean }[] = [
    { value: "current", label: t("prompt_authoring_scope_current"), disabled: !currentEntryId },
    {
      value: "pending",
      label: t("prompt_authoring_scope_pending", { count: pendingCount }),
      disabled: pendingCount === 0,
    },
    { value: "custom", label: t("prompt_authoring_scope_custom"), disabled: entries.length === 0 },
  ];

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
            <DialogTitle>{t("prompt_authoring_title")}</DialogTitle>
            <DialogDescription>{t("prompt_authoring_desc")}</DialogDescription>
          </DialogHeader>
          <DialogBody>
            <div className="flex flex-col gap-4">
              <div className="flex flex-col gap-2">
                <span id={scopeLabelId} className="text-sm font-medium">
                  {t("prompt_authoring_scope_label")}
                </span>
                <RadioGroup
                  aria-labelledby={scopeLabelId}
                  value={scope}
                  onValueChange={(next) => setScope(next as PromptAuthoringScope)}
                  disabled={submitting}
                >
                  {scopes.map((option) => (
                    <Label key={option.value}>
                      <RadioGroupItem value={option.value} disabled={option.disabled} />
                      {option.label}
                    </Label>
                  ))}
                </RadioGroup>
                {scope === "custom" && (
                  <ul
                    className="relative flex max-h-48 flex-col gap-0.5 overflow-y-auto rounded-lg border border-border p-1.5"
                    aria-label={t("prompt_authoring_scope_custom")}
                  >
                    {entries.map((entry) => (
                      <li key={entry.id} className="rounded-sm px-1.5 py-1 hover:bg-muted/50">
                        <Label className="w-full">
                          <Checkbox
                            checked={selectedSet.has(entry.id)}
                            disabled={submitting}
                            onCheckedChange={() => toggle(entry.id)}
                          />
                          <EpisodeItemRef itemId={entry.id} className="min-w-0" />
                          <span className="ml-auto text-xs text-muted-foreground">
                            {entry.pending
                              ? t("prompt_authoring_entry_pending")
                              : entry.hasContent
                                ? t("prompt_authoring_entry_has_prompt")
                                : t("prompt_authoring_entry_empty")}
                          </span>
                        </Label>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="flex items-start gap-2">
                <Checkbox
                  id={rewriteId}
                  className="mt-0.5"
                  checked={rewrite}
                  disabled={submitting}
                  onCheckedChange={(checked) => setRewrite(checked)}
                  aria-describedby={`${rewriteId}-hint`}
                />
                <div className="flex flex-col gap-0.5">
                  <Label htmlFor={rewriteId}>{t("prompt_authoring_rewrite_toggle")}</Label>
                  <p id={`${rewriteId}-hint`} className="text-xs text-muted-foreground">
                    {t(
                      rewrite
                        ? unitMode
                          ? "prompt_authoring_rewrite_hint_units"
                          : "prompt_authoring_rewrite_hint"
                        : unitMode
                          ? "prompt_authoring_fill_hint_units"
                          : "prompt_authoring_fill_hint",
                    )}
                  </p>
                </div>
              </div>

              <div className="flex flex-col gap-1.5">
                <Label htmlFor={fieldId}>{t("prompt_authoring_instructions_label")}</Label>
                <Textarea
                  id={fieldId}
                  value={instructions}
                  onChange={(event) => setInstructions(event.target.value)}
                  maxLength={4000}
                  placeholder={t("prompt_authoring_instructions_placeholder")}
                  disabled={submitting}
                />
              </div>
            </div>
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" onClick={() => void submit(null)} disabled={busy || submitting || scopeEmpty}>
              <Sparkles aria-hidden data-icon="inline-start" />
              {rewrite ? t("prompt_authoring_ai_rewrite") : t("prompt_authoring_ai_write")}
            </Button>
            <Button onClick={() => void handOff()} disabled={busy || submitting || scopeEmpty}>
              <Bot aria-hidden data-icon="inline-start" />
              {t("prompt_authoring_hand_to_agent")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <PromptOverwriteDialog
        overwrite={overwrite}
        text={overwrite ? itemIdsAsPositionsInText(overwrite.text, scripts, t) : null}
        submitting={submitting}
        confirmDisabled={busy}
        onConfirm={(revision) => void submit(revision)}
        onClose={closeOverwrite}
      />
    </>
  );
}
