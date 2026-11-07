import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import type { EpisodeDraftView } from "@/types";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useDraftEditor } from "@/hooks/useDraftEditor";
import { voidPromise } from "@/utils/async";
import { groupDraftViolations, groupSoftViolations } from "@/utils/draft-violations";
import { Textarea } from "@/components/ui/textarea";
import {
  AgentDraftBar,
  DiscardDraftDialog,
  DraftEpisodeViolations,
  DraftSoftViolationList,
  DraftViolationList,
  InvalidDraftBar,
} from "@/components/shared/DraftStatus";
import { draftFallbackText, draftFixRequestText } from "@/components/shared/draft-text";
import { prefillAssistant } from "@/components/shared/prefill-assistant";
import { useEpisodeLedger } from "@/hooks/useEpisodeLedger";
import { episodeAgentRef, itemPositionLabel } from "@/utils/episode-display";
import { findEpisodeItem } from "@/utils/episode-item";
import { ItemPositionBadge } from "@/components/shared/episode-item/ItemPositionBadge";

const DOC_TYPE = "reference_prompt_authoring";

/** Prompt-authoring draft bodies: unit bodies corresponding to formal units in script order. */
interface PromptAuthoringDraft {
  units: { text: string }[];
}

function narrowPromptAuthoringDraft(content: Record<string, unknown> | null): PromptAuthoringDraft | null {
  const units = content?.units;
  if (!Array.isArray(units)) return null;
  const valid = units.every(
    (unit: unknown) => unit != null && typeof unit === "object" && typeof (unit as { text?: unknown }).text === "string",
  );
  return valid ? (content as unknown as PromptAuthoringDraft) : null;
}

/**
 * Episode prompt-authoring drafts refetch on `draft:episode_N_prompt_authoring` events (Agent writes, adoption, discard).
 * Null without drafts.
 */
export function usePromptAuthoringDraft(projectName: string, episode: number) {
  const draftRevision = useAppStore((s) => s.getEntityRevision(`draft:episode_${episode}_prompt_authoring`));
  // Key views by project/episode so previous drafts cannot appear as current before new fetches arrive.
  const [loaded, setLoaded] = useState<{ key: string; view: EpisodeDraftView | null } | null>(null);
  const scopeKey = `${projectName}:${episode}`;
  const [reloadNonce, setReloadNonce] = useState(0);

  // react-doctor-disable-next-line react-doctor/no-set-state-after-await-in-effect -- Writes after await check signal.aborted first; cleanup abort invalidates prior rounds
  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    const load = async () => {
      const { drafts } = await API.listEpisodeDrafts(projectName, episode, { signal });
      const present = drafts.some((d) => d.doc_type === DOC_TYPE);
      const next = present ? await API.getEpisodeDraft(projectName, episode, DOC_TYPE, { signal }) : null;
      if (!signal.aborted) setLoaded({ key: scopeKey, view: next });
    };
    // Failed fetches retain views: draft panels are additional presentation, not reasons to replace unit workbenches.
    load().catch(() => undefined);
    return () => controller.abort();
  }, [projectName, episode, scopeKey, draftRevision, reloadNonce]);

  const refresh = useCallback(() => setReloadNonce((n) => n + 1), []);
  return { view: loaded?.key === scopeKey ? loaded.view : null, refresh };
}

interface PromptAuthoringDraftPanelProps {
  projectName: string;
  episode: number;
  view: EpisodeDraftView;
  onSettled: () => void;
}

/**
 * Prompt-authoring drafts on video-unit pages. Quarantined violations attach to units, episode-wide violations at top; unit bodies
 * edit/save/validate in place, writing to formal scripts when violations clear. Agent-editable drafts only indicate unfinished edits.
 */
export function PromptAuthoringDraftPanel({ projectName, episode, view, onSettled }: PromptAuthoringDraftPanelProps) {
  const { t } = useTranslation("dashboard");
  const episodeLedger = useEpisodeLedger();
  const scripts = useProjectsStore((s) => s.currentScripts);
  const episodeRef = episodeAgentRef(episodeLedger, episode, t);
  const [discardOpen, setDiscardOpen] = useState(false);
  const editor = useDraftEditor<PromptAuthoringDraft>({
    projectName,
    episode,
    view,
    narrow: narrowPromptAuthoringDraft,
    onSettled,
  });
  const itemRefs = useRef(new Map<number, HTMLElement>());
  const episodeLevelRef = useRef<HTMLElement | null>(null);
  const scrollTo = (el: HTMLElement | null | undefined) => el?.scrollIntoView({ behavior: "smooth", block: "center" });
  const busy = editor.saving || editor.discarding || editor.repairing;
  const agentOwned = view.editable_by === "agent";

  const discardDialog = (
    <DiscardDraftDialog
      open={discardOpen}
      agentOwned={agentOwned}
      fallbackText={draftFallbackText(t, view.doc_type, view.formal_exists)}
      loading={editor.discarding}
      onConfirm={async () => {
        if (await editor.discard()) setDiscardOpen(false);
      }}
      onCancel={() => setDiscardOpen(false)}
    />
  );

  if (agentOwned) {
    return (
      <>
        <AgentDraftBar
          busy={busy}
          onFinish={() => prefillAssistant(t("draft_agent_finish_prefill", { episodeRef, docType: DOC_TYPE }))}
          onDiscard={() => setDiscardOpen(true)}
        />
        {discardDialog}
      </>
    );
  }

  const content = editor.content;
  const unitIds = (content?.units ?? []).map((_, i) => view.item_ids?.[i] ?? `#${i + 1}`);
  // Drafts may cover only some units: compute formal positions now, falling back to draft position number without IDs.
  const unitPositions = unitIds.map((id, i) =>
    view.item_ids?.[i] ? (findEpisodeItem(scripts, id)?.position ?? null) : i + 1,
  );
  const unitLabel = (i: number) => itemPositionLabel("unit", unitPositions[i], t);
  const groups = groupDraftViolations(view.violations, unitIds);
  const softByUnit = groupSoftViolations(view.soft_violations);
  const updateText = (index: number, text: string) =>
    editor.setContent((prev) => ({ ...prev, units: prev.units.map((u, i) => (i === index ? { ...u, text } : u)) }));

  return (
    <div className="flex flex-col gap-3">
      <InvalidDraftBar
        title={t("draft_prompt_authoring_title")}
        violationCount={view.violations.length}
        itemJumps={[...groups.byItem.entries()].map(([index, list]) => ({
          index,
          label: unitLabel(index),
          count: list.length,
        }))}
        episodeLevelCount={groups.episodeLevel.length}
        onJump={(index) => scrollTo(itemRefs.current.get(index))}
        onJumpEpisodeLevel={() => scrollTo(episodeLevelRef.current)}
        editable={content != null}
        dirty={editor.dirty}
        saving={editor.saving}
        repairing={editor.repairing}
        onRepair={editor.repair}
        busy={busy}
        outdated={editor.outdated}
        onSave={voidPromise(editor.save)}
        onReloadLatest={editor.reloadLatest}
        onHandToAgent={() => prefillAssistant(draftFixRequestText(t, episodeRef, DOC_TYPE, view.violations))}
        onDiscard={() => setDiscardOpen(true)}
      />
      {discardDialog}
      <DraftEpisodeViolations
        violations={groups.episodeLevel}
        anchorRef={(el) => {
          episodeLevelRef.current = el;
        }}
      />
      {content?.units.map((unit, i) => {
        const violations = groups.byItem.get(i) ?? [];
        return (
          <article
            key={`${String(i)}-${unitIds[i]}`}
            ref={(el) => {
              if (el) itemRefs.current.set(i, el);
              else itemRefs.current.delete(i);
            }}
            className={`scroll-mt-28 rounded-lg border bg-card p-3.5 ${violations.length > 0 ? "border-destructive/45" : "border-border"}`}
          >
            <ItemPositionBadge position={unitPositions[i]} kind="unit" className="mb-2" />
            <Textarea
              value={unit.text}
              onChange={(e) => updateText(i, e.target.value)}
              disabled={busy}
              aria-label={t("reference_script_plan_unit_text_label", { unit: unitLabel(i) })}
            />
            <DraftViolationList violations={violations} position={unitPositions[i]} />
            <DraftSoftViolationList softViolations={softByUnit.get(i) ?? []} position={unitPositions[i]} />
          </article>
        );
      })}
    </div>
  );
}
