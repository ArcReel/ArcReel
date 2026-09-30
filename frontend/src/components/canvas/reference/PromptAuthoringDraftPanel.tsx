import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import type { EpisodeDraftView } from "@/types";
import { useAppStore } from "@/stores/app-store";
import { useDraftEditor } from "@/hooks/useDraftEditor";
import { voidPromise } from "@/utils/async";
import { groupDraftViolations, groupSoftViolations } from "@/utils/draft-violations";
import { AutoTextarea } from "@/components/ui/AutoTextarea";
import { CARD_STYLE } from "@/components/ui/darkroom-tokens";
import {
  AgentDraftBar,
  DiscardDraftDialog,
  DraftEpisodeViolations,
  DraftSoftViolationList,
  DraftViolationList,
  InvalidDraftBar,
  draftFallbackText,
  draftFixRequestText,
  prefillAssistant,
} from "@/components/shared/DraftStatus";
import { useEpisodeLedger } from "@/hooks/useEpisodeLedger";
import { episodeAgentRef, itemIdWithinEpisode } from "@/utils/episode-display";

const DOC_TYPE = "reference_prompt_authoring";

/** 提示词编写草稿的正文：按剧本顺序对应正式剧本单元的一组 unit 正文。 */
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
 * 本集的提示词编写草稿：随 `draft:episode_N_prompt_authoring` 事件（Agent 写入、采用、丢弃）重新拉取。
 * 无草稿时为 null。
 */
export function usePromptAuthoringDraft(projectName: string, episode: number) {
  const draftRevision = useAppStore((s) => s.getEntityRevision(`draft:episode_${episode}_prompt_authoring`));
  // 按所属项目与集号记下视图：切换后新一轮拉取回来前，不把上一集的草稿当作本集的呈现。
  const [loaded, setLoaded] = useState<{ key: string; view: EpisodeDraftView | null } | null>(null);
  const scopeKey = `${projectName}:${episode}`;
  const [reloadNonce, setReloadNonce] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    const load = async () => {
      const { drafts } = await API.listEpisodeDrafts(projectName, episode, { signal });
      const present = drafts.some((d) => d.doc_type === DOC_TYPE);
      const next = present ? await API.getEpisodeDraft(projectName, episode, DOC_TYPE, { signal }) : null;
      if (!signal.aborted) setLoaded({ key: scopeKey, view: next });
    };
    // 拉取失败保留现有视图：草稿面板是附加呈现，不因一次刷新失败把单元工作台换掉。
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
 * 提示词编写草稿在视频单元页的呈现。待修复草稿：违约挂到所在单元、整集层面的违约置顶，逐单元正文
 * 可就地修改后保存并校验，违约清零即写回正式剧本。Agent 的可编辑草稿：只提示有一份未完成的修改。
 */
export function PromptAuthoringDraftPanel({ projectName, episode, view, onSettled }: PromptAuthoringDraftPanelProps) {
  const { t } = useTranslation("dashboard");
  const episodeLedger = useEpisodeLedger();
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
  const busy = editor.saving || editor.discarding;
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
          label: itemIdWithinEpisode(unitIds[index]),
          count: list.length,
        }))}
        episodeLevelCount={groups.episodeLevel.length}
        onJump={(index) => scrollTo(itemRefs.current.get(index))}
        onJumpEpisodeLevel={() => scrollTo(episodeLevelRef.current)}
        editable={content != null}
        dirty={editor.dirty}
        saving={editor.saving}
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
            className={`scroll-mt-28 rounded-[10px] border p-3.5 ${violations.length > 0 ? "border-red-500/45" : "border-hairline"}`}
            style={CARD_STYLE}
          >
            <span className="mb-2 inline-block rounded bg-bg-grad-a/70 px-1.5 py-0.5 font-mono text-[11px] text-text-2">
              {itemIdWithinEpisode(unitIds[i])}
            </span>
            <AutoTextarea
              value={unit.text}
              onChange={(text) => updateText(i, text)}
              disabled={busy}
              aria-label={t("reference_script_plan_unit_text_label", { unit: itemIdWithinEpisode(unitIds[i]) })}
              className="text-text-3"
            />
            <DraftViolationList violations={violations} />
            <DraftSoftViolationList softViolations={softByUnit.get(i) ?? []} />
          </article>
        );
      })}
    </div>
  );
}
