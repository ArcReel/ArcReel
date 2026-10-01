import { useEffect, useId, useMemo, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { Bot, Sparkles } from "lucide-react";
import { API, ApiRequestError } from "@/api";
import { enqueuePromptAuthoring, promptAuthoringResourceId } from "@/actions/generation";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { GlassModal } from "@/components/ui/GlassModal";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import {
  usePromptAuthoringStore,
  type PromptAuthoringOpenRequest,
  type PromptAuthoringScope,
} from "@/stores/prompt-authoring-store";
import { isResourceBusy } from "@/stores/tasks-store";
import type { PromptOverwrite } from "@/types";
import { errMsg } from "@/utils/async";
import { useEpisodeLedger } from "@/hooks/useEpisodeLedger";
import { episodeAgentRef, itemIdsInEpisodeText } from "@/utils/episode-display";
import { promptAuthoringEntries, type PromptAuthoringEntry } from "./prompt-authoring-entries";
import { promptAuthoringHandoffText } from "./prompt-authoring-handoff";

function readPromptOverwrite(err: unknown): PromptOverwrite | null {
  if (!(err instanceof ApiRequestError) || err.status !== 409) return null;
  const diagnostic = err.diagnostic as { prompt_overwrite?: PromptOverwrite } | undefined;
  return diagnostic?.prompt_overwrite ?? null;
}

const FIELD_STYLE: CSSProperties = {
  background: "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.6), oklch(0.18 0.010 265 / 0.45))",
  border: "1px solid var(--color-hairline)",
  color: "var(--color-text)",
  boxShadow: "inset 0 1px 2px oklch(0 0 0 / 0.2)",
};

interface HostProps {
  projectName: string;
  episode: number;
  /** 本集正式脚本；尚无时不渲染。 */
  script: unknown;
  /** 本集上次保存的附加指令。 */
  savedInstructions?: string;
}

/** 集页上唯一的「编写提示词」弹窗宿主：按 {@link usePromptAuthoringStore} 的请求打开。 */
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
      // 每次打开都从请求重新初始化范围与指令，不沿用上一次弹窗里未提交的状态。
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
  /** 参考生视频单元：视觉层是单元正文，提示文案按单元说。 */
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
  const episodeLedger = useEpisodeLedger();
  const titleId = useId();
  const descId = useId();
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
  const [submitting, setSubmitting] = useState(false);
  const [overwrite, setOverwrite] = useState<PromptOverwrite | null>(null);

  // 脚本在弹窗打开期间变化（条目被删）时，自选范围只算仍在脚本里的条目，按剧本顺序。
  const selectedIds = useMemo(
    () => entries.filter((entry) => selected.includes(entry.id)).map((entry) => entry.id),
    [entries, selected],
  );

  const entryIds: string[] | null =
    scope === "current" ? (currentEntryId ? [currentEntryId] : []) : scope === "custom" ? selectedIds : null;
  const scopeEmpty = entryIds === null ? pendingCount === 0 : entryIds.length === 0;
  const scopeLabel =
    scope === "pending"
      ? t("prompt_authoring_scope_pending_prefill", { count: pendingCount })
      : (entryIds ?? []).join(t("prompt_authoring_id_separator"));

  const guardBusy = (): boolean => {
    if (isResourceBusy("text_episode_script", projectName, promptAuthoringResourceId(episode))) {
      useAppStore.getState().pushToast(t("prompt_authoring_busy"), "error");
      return true;
    }
    return false;
  };

  const submit = async (overwriteRevision: string | null) => {
    if (submitting || scopeEmpty || guardBusy()) return;
    setSubmitting(true);
    try {
      await enqueuePromptAuthoring(projectName, episode, {
        entry_ids: entryIds,
        rewrite,
        instructions: instructions.trim() || null,
        overwrite_revision: overwriteRevision,
      });
      setOverwrite(null);
      onClose();
    } catch (err) {
      const loss = readPromptOverwrite(err);
      if (loss) {
        setOverwrite(loss);
      } else {
        setOverwrite(null);
        useAppStore.getState().pushToast(errMsg(err), "error");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handOff = async () => {
    if (submitting || scopeEmpty) return;
    setSubmitting(true);
    try {
      await API.savePromptAuthoringInstructions(projectName, episode, instructions.trim());
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
      setSubmitting(false);
      return;
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
    setSubmitting(false);
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
            {t("prompt_authoring_title")}
          </h2>
          <p id={descId} className="mt-1.5 text-[12.5px] leading-[1.55]" style={{ color: "var(--color-text-3)" }}>
            {t("prompt_authoring_desc")}
          </p>

          <fieldset className="mt-4">
            <legend className="text-[12px] font-medium" style={{ color: "var(--color-text-2)" }}>
              {t("prompt_authoring_scope_label")}
            </legend>
            <div className="mt-1.5 flex flex-wrap gap-1.5" role="radiogroup">
              {scopes.map((option) => (
                <label
                  key={option.value}
                  className={`inline-flex focus-within:ring-1 focus-within:ring-[var(--color-accent)] cursor-pointer items-center gap-1.5 rounded-md px-2.5 py-1 text-[12px] ${
                    option.disabled ? "cursor-not-allowed opacity-45" : ""
                  }`}
                  style={{
                    border: `1px solid ${scope === option.value ? "var(--color-accent-soft)" : "var(--color-hairline)"}`,
                    background: scope === option.value ? "var(--color-accent-dim)" : "transparent",
                    color: scope === option.value ? "var(--color-text)" : "var(--color-text-2)",
                  }}
                >
                  <input
                    type="radio"
                    name={`${titleId}-scope`}
                    value={option.value}
                    checked={scope === option.value}
                    disabled={option.disabled || submitting}
                    onChange={() => setScope(option.value)}
                    className="sr-only"
                  />
                  {option.label}
                </label>
              ))}
            </div>
          </fieldset>

          {scope === "custom" && (
            <ul
              className="mt-2 max-h-48 space-y-0.5 overflow-y-auto rounded-lg p-1.5"
              style={{ border: "1px solid var(--color-hairline)" }}
              aria-label={t("prompt_authoring_scope_custom")}
            >
              {entries.map((entry) => (
                <li key={entry.id}>
                  <label className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-[12px] hover:bg-[oklch(1_0_0_/_0.04)]">
                    <input
                      type="checkbox"
                      checked={selected.includes(entry.id)}
                      disabled={submitting}
                      onChange={() => toggle(entry.id)}
                    />
                    <span className="font-mono" style={{ color: "var(--color-text)" }}>
                      {entry.id}
                    </span>
                    <span className="ml-auto text-[11px]" style={{ color: "var(--color-text-4)" }}>
                      {entry.pending
                        ? t("prompt_authoring_entry_pending")
                        : entry.hasContent
                          ? t("prompt_authoring_entry_has_prompt")
                          : t("prompt_authoring_entry_empty")}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}

          <label className="mt-4 flex cursor-pointer items-start gap-2 text-[12.5px]" style={{ color: "var(--color-text-2)" }}>
            <input
              type="checkbox"
              className="mt-0.5"
              checked={rewrite}
              disabled={submitting}
              onChange={(event) => setRewrite(event.target.checked)}
            />
            <span>
              {t("prompt_authoring_rewrite_toggle")}
              <span className="mt-0.5 block text-[11.5px]" style={{ color: "var(--color-text-4)" }}>
                {t(
                  rewrite
                    ? unitMode
                      ? "prompt_authoring_rewrite_hint_units"
                      : "prompt_authoring_rewrite_hint"
                    : unitMode
                      ? "prompt_authoring_fill_hint_units"
                      : "prompt_authoring_fill_hint",
                )}
              </span>
            </span>
          </label>

          <label htmlFor={fieldId} className="mt-4 block text-[12px] font-medium" style={{ color: "var(--color-text-2)" }}>
            {t("prompt_authoring_instructions_label")}
          </label>
          <textarea
            id={fieldId}
            value={instructions}
            onChange={(event) => setInstructions(event.target.value)}
            rows={3}
            maxLength={4000}
            placeholder={t("prompt_authoring_instructions_placeholder")}
            className="focus-ring mt-1.5 w-full resize-none rounded-lg px-3 py-2 text-[13px] leading-[1.55] outline-none"
            style={FIELD_STYLE}
          />

          <div className="mt-4 flex items-center justify-end gap-2">
            <SecondaryButton
              size="sm"
              onClick={() => void submit(null)}
              disabled={submitting || scopeEmpty}
              leadingIcon={<Sparkles className="h-3.5 w-3.5" aria-hidden="true" />}
            >
              {rewrite ? t("prompt_authoring_ai_rewrite") : t("prompt_authoring_ai_write")}
            </SecondaryButton>
            <PrimaryButton
              size="sm"
              onClick={() => void handOff()}
              disabled={submitting || scopeEmpty}
              leadingIcon={<Bot className="h-3.5 w-3.5" aria-hidden="true" />}
            >
              {t("prompt_authoring_hand_to_agent")}
            </PrimaryButton>
          </div>
        </div>
      </GlassModal>

      <ConfirmDialog
        open={overwrite !== null}
        tone="danger"
        title={t("prompt_authoring_overwrite_title")}
        description={<p className="whitespace-pre-line">{overwrite ? itemIdsInEpisodeText(overwrite.text) : null}</p>}
        confirmLabel={t("prompt_authoring_ai_rewrite")}
        loading={submitting}
        onConfirm={() => void submit(overwrite?.revision ?? null)}
        onCancel={() => setOverwrite(null)}
      />
    </>
  );
}
