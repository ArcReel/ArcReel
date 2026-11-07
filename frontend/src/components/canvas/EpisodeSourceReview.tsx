import { useCallback, useEffect, useId, useMemo, useRef, useState, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import { Anchor, ChevronRight } from "lucide-react";
import { Link } from "wouter";
import { cn } from "cn";
import { API } from "@/api";
import { useStaysInEpisodeView } from "@/components/canvas/episode-page/EpisodeViewScope";
import { ImpactConfirmDialog } from "@/components/canvas/episodes/ImpactConfirmDialog";
import { SourceKindSelect } from "@/components/canvas/episodes/SourceKindSelect";
import { episodesViewPath } from "@/components/canvas/episodes/episodes-view-model";
import { ScriptPlanStart } from "@/components/canvas/shared/ScriptPlanStart";
import { UnsavedChangesBar } from "@/components/shared/edit-unit/UnsavedChangesBar";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Textarea } from "@/components/ui/textarea";
import { useEpisodeSurfaceRequest } from "@/stores/episode-surface-store";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";
import type { EpisodeMeta } from "@/types";
import type { SourceKind } from "@/types/episodes-view";
import { episodeDisplayName, episodePosition } from "@/utils/episode-display";

/**
 * Script-planning tab start state: neither script plan nor formal script exists. Start area (sole first-planning entry) above
 * episode source; episode guide sits in a sticky right sidebar at widths of at least 860px, otherwise collapsible above source.
 * Applies to narration, drama, and reference-to-video; episode pages exclude single-episode ads without source slices.
 *
 * Source handling follows origins: whole-source cut episodes are read-only (files derive from episode planning; edit in Episodes view);
 * own-source/sourceless episodes edit in place as one editing unit. Drama projects also select source kind;
 * confirm before saving when kind changes would outdate this episode's existing script plan.
 */

type SourceOrigin = NonNullable<EpisodeMeta["source_origin"]>;

function sourceOriginOf(meta: EpisodeMeta | undefined): SourceOrigin {
  return meta?.source_origin ?? (meta?.source_range ? "whole_source" : "none");
}

// ---------------------------------------------------------------------------
// Episode guide: beats retain ordered numbering; ending hook gets its own block.
// ---------------------------------------------------------------------------

function hasGuide(meta: EpisodeMeta | undefined): boolean {
  return (meta?.outline?.story_beats?.length ?? 0) > 0 || Boolean(meta?.hook);
}

function GuideContent({ meta }: { meta: EpisodeMeta | undefined }) {
  const { t } = useTranslation("dashboard");
  const beats = meta?.outline?.story_beats ?? [];
  const hook = meta?.hook;
  return (
    <div className="flex flex-col gap-3">
      {beats.length > 0 && (
        <ol className="flex flex-col gap-2.5">
          {beats.map((beat, index) => (
            // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- Beats are read-only ordered text without reordering/additions/deletions or row state; position identifies them and repeated text cannot be keys
            <li key={index} className="flex gap-2.5 text-sm leading-relaxed">
              <span aria-hidden className="w-4 shrink-0 text-right font-semibold text-primary tabular-nums">
                {index + 1}
              </span>
              <span className="min-w-0 text-subtle-foreground">{beat}</span>
            </li>
          ))}
        </ol>
      )}
      {hook && (
        <div className="flex items-start gap-2.5 rounded-lg bg-primary/10 px-3 py-2.5 text-sm leading-relaxed">
          <Anchor aria-hidden className="mt-1 size-3.5 shrink-0 text-primary" />
          <p className="min-w-0 text-subtle-foreground">
            <span className="mr-1.5 font-semibold text-primary">{t("episode_workspace_guide_hook")}</span>
            {hook}
          </p>
        </div>
      )}
    </div>
  );
}

function guideSummary(meta: EpisodeMeta | undefined, t: (key: string, options?: Record<string, unknown>) => string) {
  const beats = meta?.outline?.story_beats?.length ?? 0;
  return [
    beats > 0 ? t("episode_workspace_guide_beats", { count: beats }) : null,
    meta?.hook ? t("episode_workspace_guide_hook") : null,
  ].filter((part): part is string => part !== null);
}

/** Narrow view: guide above source, expanded by default and collapsible. */
function GuideCollapsible({ meta, className }: { meta: EpisodeMeta | undefined; className?: string }) {
  const { t } = useTranslation("dashboard");
  return (
    <div className={cn("rounded-xl border border-border p-1", className)}>
      <Collapsible defaultOpen>
        <CollapsibleTrigger render={<Button variant="ghost" size="sm" className="w-full justify-start" />}>
          <ChevronRight aria-hidden data-icon="inline-start" className="group-aria-expanded/button:rotate-90" />
          <span className="font-medium text-foreground">{t("episode_workspace_guide_title")}</span>
          {guideSummary(meta, t).map((part) => (
            <span key={part} className="font-normal text-muted-foreground">
              {part}
            </span>
          ))}
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="px-2.5 pt-2 pb-2">
            <GuideContent meta={meta} />
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}

/** Wide view: guide in right sidebar, sticky during scrolling. */
function GuideRail({ meta, className }: { meta: EpisodeMeta | undefined; className?: string }) {
  const { t } = useTranslation("dashboard");
  const titleId = useId();
  return (
    <aside aria-labelledby={titleId} className={className}>
      <div className="flex flex-col gap-3">
        <h2 id={titleId} className="text-sm font-medium text-muted-foreground">
          {t("episode_workspace_guide_title")}
        </h2>
        <GuideContent meta={meta} />
      </div>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Episode source
// ---------------------------------------------------------------------------

/** Slice origin: filename, start/end offsets, approximate character count; cross-file offsets cannot subtract, so show only start/end files. */
function SourceRangeNote({ meta }: { meta: EpisodeMeta | undefined }) {
  const { t } = useTranslation("dashboard");
  const r = meta?.source_range;
  if (!r) return null;
  const crossesFiles = r.end_file != null && r.end_file !== r.source_file;
  const fileName = (path: string | undefined) => path?.replace(/^source\//, "");
  const parts = crossesFiles
    ? [`${fileName(r.source_file)} – ${fileName(r.end_file)}`]
    : [
        fileName(r.source_file),
        r.start != null && r.end != null ? `${r.start.toLocaleString()}–${r.end.toLocaleString()}` : null,
        r.start != null && r.end != null
          ? t("episode_workspace_chars_approx", { count: (r.end - r.start).toLocaleString() })
          : null,
      ];
  return (
    <p className="flex min-w-0 flex-wrap gap-x-3 text-xs text-muted-foreground tabular-nums">
      {parts.filter(Boolean).map((part) => (
        <span key={part}>
          {part}
        </span>
      ))}
    </p>
  );
}

const READING_CLS = "max-w-[40em] font-editorial text-base leading-loose whitespace-pre-wrap text-subtle-foreground";

interface SourceValue {
  text: string;
  /** Source kind; null omits selection for non-drama projects. */
  kind: SourceKind | null;
}

/** Source editing-unit handle for same-page planning; planning reads saved source. */
interface SourceEditHandle {
  /** Save unsaved edits and return whether continuation is allowed; true without changes, false while saving. */
  save: () => Promise<boolean>;
}

/** Own-source/sourceless episodes edit in place, confirming kind changes if needed before saving; callers adopt content/refresh afterward. */
function SourceEditor({
  projectName,
  episode,
  episodes,
  saved,
  onSaved,
  editRef,
}: {
  projectName: string;
  episode: number;
  episodes: EpisodeMeta[];
  saved: SourceValue;
  onSaved: (text: string) => void;
  editRef: RefObject<SourceEditHandle | null>;
}) {
  const { t } = useTranslation("dashboard");
  const fieldRef = useRef<HTMLTextAreaElement>(null);
  const barRef = useRef<HTMLDivElement>(null);
  // Kind-change confirmation: affected episodes and the save waiting for its outcome.
  const [pending, setPending] = useState<number[] | null>(null);
  const [confirming, setConfirming] = useState(false);
  const resolveRef = useRef<((confirmed: boolean) => void) | null>(null);
  const settle = (confirmed: boolean) => {
    resolveRef.current?.(confirmed);
    resolveRef.current = null;
  };
  // If unmounted while awaiting confirmation, settle as cancellation, failing the save.
  useEffect(() => () => resolveRef.current?.(false), []);

  const save = useCallback(
    async (value: SourceValue) => {
      if (value.text.trim() === "") throw new Error(t("episode_workspace_source_blank"));
      const kind = value.kind ?? undefined;
      const first = await API.updateEpisodeSource(projectName, episode, value.text, kind, false);
      if (first.needs_confirmation) {
        const confirmed = await new Promise<boolean>((resolve) => {
          resolveRef.current = resolve;
          setPending(first.affected_episodes);
        });
        if (!confirmed) {
          setPending(null);
          throw new Error(t("episode_workspace_source_kind_declined"));
        }
        setConfirming(true);
        try {
          await API.updateEpisodeSource(projectName, episode, value.text, kind, true);
        } finally {
          setConfirming(false);
          setPending(null);
        }
      }
      onSaved(value.text);
      void refreshAfterWrite(projectName, t);
      return value;
    },
    [projectName, episode, onSaved, t],
  );
  // Allow only navigation staying in script planning; Episodes view, other episodes/views still ask.
  const allowNavigation = useStaysInEpisodeView();
  const unit = useEditUnit({ source: saved, save, allowNavigation });

  const saveUnit = unit.save;
  const saving = unit.status === "saving";
  useEffect(() => {
    editRef.current = {
      save: async () => {
        if (saving) return false;
        const done = await saveUnit();
        // Failed-save reasons appear below source; scroll into view and do not submit planning.
        if (!done) barRef.current?.scrollIntoView({ block: "nearest" });
        return done;
      },
    };
    return () => {
      editRef.current = null;
    };
  }, [editRef, saveUnit, saving]);

  useEpisodeSurfaceRequest(projectName, episode, "episode_source", () => {
    fieldRef.current?.focus();
    fieldRef.current?.scrollIntoView({ block: "center" });
  });

  return (
    <div className="flex flex-col gap-3">
      {saved.text === "" && <p className="text-sm text-muted-foreground">{t("episode_workspace_source_empty_hint")}</p>}
      {unit.value.kind !== null && (
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          {t("source_kind")}
          <SourceKindSelect
            value={unit.value.kind}
            onChange={(kind) => unit.setValue((prev) => ({ ...prev, kind }))}
            disabled={saving}
            label={t("source_kind")}
          />
        </label>
      )}
      {/* Editable body uses the read-only source font family; input height follows content with overall start-state scrolling. */}
      <div className="max-w-[calc(40em+1.25rem)] font-editorial">
        <Textarea
          ref={fieldRef}
          variant="plain"
          value={unit.value.text}
          onChange={(event) => unit.setValue((prev) => ({ ...prev, text: event.target.value }))}
          // Read-only while saving: planning awaits saved content; further edits could make planning use old content.
          readOnly={saving}
          placeholder={t("episode_workspace_source_placeholder")}
          aria-label={t("episode_workspace_source_title")}
          className="-mx-2.5 min-h-40 max-h-none"
        />
      </div>
      <div ref={barRef}>
        <UnsavedChangesBar unit={unit} className="max-w-[40em]" />
      </div>
      <ImpactConfirmDialog
        request={
          pending && {
            title: t("source_kind_change_episode_title"),
            body: (
              <div className="flex flex-col gap-2">
                <p>{t("source_kind_change_episode_desc")}</p>
                <ul className="list-disc pl-5">
                  {pending.map((affected) => (
                    <li key={affected}>
                      {t("source_kind_change_episode", {
                        position: episodePosition(episodes, affected) ?? "?",
                        name: episodeDisplayName(episodes, affected, t),
                      })}
                    </li>
                  ))}
                </ul>
              </div>
            ),
            confirmLabel: t("source_kind_change_episode_confirm"),
            destructive: false,
          }
        }
        busy={confirming}
        onConfirm={() => settle(true)}
        onCancel={() => settle(false)}
      />
    </div>
  );
}

function EpisodeSource({
  projectName,
  episode,
  episodes,
  meta,
  editRef,
}: {
  projectName: string;
  episode: number;
  episodes: EpisodeMeta[];
  meta: EpisodeMeta | undefined;
  editRef: RefObject<SourceEditHandle | null>;
}) {
  const { t } = useTranslation("dashboard");
  const titleId = useId();
  const isDrama = useProjectsStore((s) => s.currentProjectData?.content_mode === "drama");
  const origin = sourceOriginOf(meta);
  const editable = origin !== "whole_source";
  // Sourceless episodes have no registered source file; same-name episode_N.txt on disk is unregistered, not read as this source.
  const withoutSource = meta !== undefined && origin === "none";
  const fetchKey = `${projectName}::${episode}`;
  const sourceRevision = useAppStore((s) => s.getEntityRevision(`episode:${episode}`) + s.getEntityRevision("project:project"));
  // Loaded source carries ownership keys; matching keys determine loading. Sourceless episodes show only contents just saved here.
  const [fetched, setFetched] = useState<{ key: string; text: string | null } | null>(null);

  useEffect(() => {
    if (withoutSource) return;
    const controller = new AbortController();
    void API.getSourceContent(projectName, `episode_${episode}.txt`, { signal: controller.signal })
      .catch(() => null)
      .then((text) => {
        if (!controller.signal.aborted) setFetched({ key: `${projectName}::${episode}`, text });
      });
    return () => controller.abort();
  }, [projectName, episode, withoutSource, sourceRevision]);

  const loading = !withoutSource && fetched?.key !== fetchKey;
  const text = fetched?.key === fetchKey ? fetched.text : null;
  const savedKind = isDrama ? (meta?.source_kind ?? "novel") : null;
  const saved = useMemo<SourceValue>(() => ({ text: text ?? "", kind: savedKind }), [text, savedKind]);
  const handleSaved = useCallback((value: string) => setFetched({ key: fetchKey, text: value }), [fetchKey]);

  let body;
  if (loading) {
    body = (
      <p role="status" className="text-sm text-muted-foreground">
        {t("episode_workspace_source_loading")}
      </p>
    );
  } else if (editable) {
    body = (
      <SourceEditor
        key={fetchKey}
        projectName={projectName}
        episode={episode}
        episodes={episodes}
        saved={saved}
        onSaved={handleSaved}
        editRef={editRef}
      />
    );
  } else if (text) {
    body = <p className={READING_CLS}>{text}</p>;
  } else {
    body = <p className="text-sm text-muted-foreground">{t("episode_workspace_source_missing")}</p>;
  }

  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h2 id={titleId} className="text-base font-semibold text-foreground">
          {t("episode_workspace_source_title")}
        </h2>
        <SourceRangeNote meta={meta} />
        {!editable && (
          <p className="text-sm text-muted-foreground">
            {t("episode_workspace_source_readonly_hint")}{" "}
            <Link
              href={episodesViewPath({ episode })}
              className="focus-ring rounded-sm text-primary underline underline-offset-2"
            >
              {t("episode_workspace_source_go_episodes")}
            </Link>
          </p>
        )}
      </div>
      {body}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export function EpisodeSourceReview({
  projectName,
  episode,
  episodes,
}: {
  projectName: string;
  episode: number;
  episodes: EpisodeMeta[];
}) {
  const meta = episodes.find((e) => e.episode === episode);
  const guide = hasGuide(meta);
  // Planning reads saved source: save same-page edits first; failures or unconfirmed kind changes prevent submission.
  const sourceRef = useRef<SourceEditHandle | null>(null);
  const saveSource = useCallback(() => sourceRef.current?.save() ?? Promise.resolve(true), []);
  return (
    <div className="@container/plan-start relative flex min-h-0 flex-1 flex-col overflow-y-auto [scrollbar-gutter:stable]">
      <div
        className={
          guide
            ? "mx-auto grid w-full max-w-280 gap-x-10 gap-y-6 px-6 pt-6 pb-16 @min-[860px]/plan-start:grid-cols-[minmax(0,1fr)_minmax(260px,320px)]"
            : "mx-auto grid w-full max-w-190 gap-y-6 px-6 pt-6 pb-16"
        }
      >
        <div className="flex min-w-0 flex-col gap-6">
          <ScriptPlanStart
            projectName={projectName}
            episode={episode}
            savedInstructions={meta?.script_plan_instructions ?? ""}
            prepare={saveSource}
          />
          {guide && <GuideCollapsible key={episode} meta={meta} className="@min-[860px]/plan-start:hidden" />}
          <EpisodeSource projectName={projectName} episode={episode} episodes={episodes} meta={meta} editRef={sourceRef} />
        </div>
        {guide && (
          <GuideRail
            meta={meta}
            className="hidden self-start @min-[860px]/plan-start:sticky @min-[860px]/plan-start:top-6 @min-[860px]/plan-start:block"
          />
        )}
      </div>
    </div>
  );
}
