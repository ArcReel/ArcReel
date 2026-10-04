import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
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
import { useProjectsStore } from "@/stores/projects-store";
import type { EpisodeMeta } from "@/types";
import type { SourceKind } from "@/types/episodes-view";
import { episodeDisplayName, episodePosition } from "@/utils/episode-display";

/**
 * 「脚本规划」tab 的起步态：本集既没有脚本规划也没有正式脚本。自上而下是起步区（首次规划的唯一入口）
 * 与本集原文；本集导览在视图宽 860px 以上放右侧栏并随滚动固定，更窄时回到原文上方、可折叠。
 * 适用 narration、drama 与参考生视频；广告项目恒单集、没有源文切片，由集页排除。
 *
 * 本集原文按来源区分：切自整本源文的集只读（集文件由分集规划派生，去「分集」视图修改）；自带原文的集
 * 与无原文的集就地编辑，原文是一个编辑单元。剧情演绎项目一并选源文件类型，改类型会让本集已有的
 * 脚本规划过期时，保存前先确认。
 */

type SourceOrigin = NonNullable<EpisodeMeta["source_origin"]>;

function sourceOriginOf(meta: EpisodeMeta | undefined): SourceOrigin {
  return meta?.source_origin ?? (meta?.source_range ? "whole_source" : "none");
}

// ---------------------------------------------------------------------------
// 本集导览：节拍是顺序，保留编号；尾钩子单独一块
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

/** 窄视图：导览放在原文上方，默认展开，可以收起。 */
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

/** 宽视图：导览在右侧栏，随滚动固定在视野里。 */
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
// 本集原文
// ---------------------------------------------------------------------------

/** 切片的来源说明：文件名、起止偏移与约略字数；跨文件的切片起止偏移不能相减，只写起止文件。 */
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
  /** 源文件类型；null 时不提供类型选择（非剧情演绎项目）。 */
  kind: SourceKind | null;
}

/** 自带原文与无原文的集：原文就地编辑，保存前按需确认改类型。保存后由调用方采用新内容并刷新项目。 */
function SourceEditor({
  projectName,
  episode,
  episodes,
  saved,
  onSaved,
}: {
  projectName: string;
  episode: number;
  episodes: EpisodeMeta[];
  saved: SourceValue;
  onSaved: (text: string) => void;
}) {
  const { t } = useTranslation("dashboard");
  const fieldRef = useRef<HTMLTextAreaElement>(null);
  // 改类型的确认：受影响的集，以及等确认结果的保存
  const [pending, setPending] = useState<number[] | null>(null);
  const [confirming, setConfirming] = useState(false);
  const resolveRef = useRef<((confirmed: boolean) => void) | null>(null);
  const settle = (confirmed: boolean) => {
    resolveRef.current?.(confirmed);
    resolveRef.current = null;
  };
  // 卸载时还在等确认：按取消结算，保存随之失败
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
      void useProjectsStore.getState().refreshProject(projectName);
      return value;
    },
    [projectName, episode, onSaved, t],
  );
  // 只放行仍停留在脚本规划视图的跳转；去分集、别的集或别的视图照常询问
  const allowNavigation = useStaysInEpisodeView();
  const unit = useEditUnit({ source: saved, save, allowNavigation });

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
            disabled={unit.status === "saving"}
            label={t("source_kind")}
          />
        </label>
      )}
      {/* 正文用编辑体，与只读的原文同一字族；框随内容撑高，由起步态整体滚动 */}
      <div className="max-w-[calc(40em+1.25rem)] font-editorial">
        <Textarea
          ref={fieldRef}
          variant="plain"
          value={unit.value.text}
          onChange={(event) => unit.setValue((prev) => ({ ...prev, text: event.target.value }))}
          placeholder={t("episode_workspace_source_placeholder")}
          aria-label={t("episode_workspace_source_title")}
          className="-mx-2.5 min-h-40 max-h-none"
        />
      </div>
      <UnsavedChangesBar unit={unit} className="max-w-[40em]" />
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
}: {
  projectName: string;
  episode: number;
  episodes: EpisodeMeta[];
  meta: EpisodeMeta | undefined;
}) {
  const { t } = useTranslation("dashboard");
  const titleId = useId();
  const isDrama = useProjectsStore((s) => s.currentProjectData?.content_mode === "drama");
  const origin = sourceOriginOf(meta);
  const editable = origin !== "whole_source";
  // 无原文的集没有集原文文件：盘上同名的 episode_N.txt 是未登记文件，不当作本集原文读取
  const withoutSource = meta !== undefined && origin === "none";
  const fetchKey = `${projectName}::${episode}`;
  // 取到的原文带上归属 key，加载中由 key 是否匹配派生；无原文的集只显示本页刚保存的内容
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
  }, [projectName, episode, withoutSource]);

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
// 入口
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
          />
          {guide && <GuideCollapsible key={episode} meta={meta} className="@min-[860px]/plan-start:hidden" />}
          <EpisodeSource projectName={projectName} episode={episode} episodes={episodes} meta={meta} />
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
