import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Anchor, ChevronDown, Loader2, PencilLine } from "lucide-react";
import { API } from "@/api";
import { ScriptPlanButton } from "@/components/canvas/shared/ScriptPlanButton";
import { StartBlankScriptButton } from "@/components/canvas/shared/StartBlankScriptButton";
import { useScriptPlanEntry } from "@/hooks/useScriptPlanEntry";
import { useAppStore } from "@/stores/app-store";
import { useEpisodeSurfaceRequest } from "@/stores/episode-surface-store";
import { useProjectsStore } from "@/stores/projects-store";
import { EditableEpisodeTitle } from "@/components/canvas/EditableEpisodeTitle";
import { EpisodeDeleteButton } from "@/components/canvas/episodes/EpisodeDeleteButton";
import { SourceKindSelect } from "@/components/canvas/episodes/SourceKindSelect";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import type { EpisodeMeta } from "@/types";
import type { SourceKind } from "@/types/episodes-view";
import { errMsg } from "@/utils/async";
import { episodeDisplayName, episodePosition } from "@/utils/episode-display";

/**
 * 已选集但既没有脚本规划也没有正式脚本时的画布视图：呈现本集原文与分集元信息（边界、节拍、
 * 尾钩子），标题行放脚本的起步入口；AI 规划脚本在跑时显示任务进度，完成后集页转入内容确认。
 * 适用 narration/drama 全部生成路径；ad 恒单集无源文切片，由 StudioCanvasRouter 排除。
 *
 * 本集原文按来源区分：切自整本源文的集只读（集文件由分集规划派生）；自带原文的集可改写；
 * 无原文的集直接给出填写框，保存后转为自带原文的集。剧情演绎项目填写或改写时一并选源文件类型；
 * 改类型会让本集已有的脚本规划过期时，先请创作者确认再保存。
 */

type SourceOrigin = NonNullable<EpisodeMeta["source_origin"]>;

function sourceOriginOf(meta: EpisodeMeta | undefined): SourceOrigin {
  return meta?.source_origin ?? (meta?.source_range ? "whole_source" : "none");
}

// ---------------------------------------------------------------------------
// 标题区：播出位置徽标 + 标题 + 状态 chip + 删除 + 源文元信息 + 起步入口
// ---------------------------------------------------------------------------

function EpisodeHeader({
  episode,
  episodes,
  meta,
  onSaveTitle,
  actions,
}: {
  episode: number;
  episodes: EpisodeMeta[];
  meta: EpisodeMeta | undefined;
  onSaveTitle: (next: string) => Promise<void>;
  actions: React.ReactNode;
}) {
  const { t } = useTranslation("dashboard");
  const position = episodePosition(episodes, episode);
  const r = meta?.source_range;
  // 跨文件的原文范围：起止偏移在不同文件里，不能直接相减，只显示起止文件
  const crossesFiles = r?.end_file != null && r.end_file !== r.source_file;
  const chars = !crossesFiles && r?.start != null && r?.end != null ? r.end - r.start : null;
  const fileName = (path: string | undefined) => path?.replace(/^source\//, "");
  const sourceName = crossesFiles ? `${fileName(r?.source_file)} – ${fileName(r?.end_file)}` : fileName(r?.source_file);
  return (
    <header className="flex items-start gap-3.5">
      <div
        className="num grid h-11 w-11 shrink-0 place-items-center rounded-lg text-[13px] font-bold"
        style={{
          background: "linear-gradient(135deg, var(--color-accent) 0%, oklch(0.45 0.12 285) 100%)",
          color: "oklch(0.14 0 0)",
          boxShadow:
            "inset 0 1px 0 oklch(1 0 0 / 0.25), 0 0 0 1px oklch(1 0 0 / 0.12), 0 4px 12px -4px var(--color-accent-glow)",
        }}
      >
        {position ?? "—"}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2.5">
          <EditableEpisodeTitle
            title={meta?.title ?? ""}
            placeholder={episodeDisplayName(episodes, episode, t)}
            canEdit={meta !== undefined}
            onSave={onSaveTitle}
            headingClassName="truncate text-[17px] font-semibold leading-tight"
            headingStyle={{ color: "var(--color-text)" }}
          />
          <span
            className="shrink-0 rounded-full px-2.5 py-0.5 text-[10.5px]"
            style={{
              color: "var(--color-warm)",
              background: "var(--color-warm-soft)",
              border: "1px solid var(--color-warm-ring)",
            }}
          >
            {t("episode_workspace_script_pending")}
          </span>
          <EpisodeDeleteButton episode={episode} />
        </div>
        <div className="mt-1 flex items-center gap-1.5 text-[11px]" style={{ color: "var(--color-text-4)" }}>
          {sourceName ? <span className="truncate">{sourceName}</span> : null}
          {!crossesFiles && r?.start != null && r?.end != null ? (
            <>
              <span aria-hidden>·</span>
              <span className="num shrink-0">
                {r.start.toLocaleString()}–{r.end.toLocaleString()}
              </span>
            </>
          ) : null}
          {chars != null ? (
            <>
              <span aria-hidden>·</span>
              <span className="num shrink-0">
                {t("episode_workspace_chars_approx", { count: chars.toLocaleString() })}
              </span>
            </>
          ) : null}
        </div>
      </div>
      <div className="mt-0.5 flex shrink-0 items-center gap-2">{actions}</div>
    </header>
  );
}

// ---------------------------------------------------------------------------
// AI 规划脚本的任务进度：排队 / 生成中。上一次失败的原因由集页顶部的文本任务失败条呈现
// ---------------------------------------------------------------------------

function ScriptPlanProgress({ projectName, episode }: { projectName: string; episode: number }) {
  const { t } = useTranslation("dashboard");
  const { busy, latestTask } = useScriptPlanEntry(projectName, episode);
  if (!busy) return null;
  return (
    <div
      role="status"
      className="mt-4 flex items-center gap-2.5 rounded-xl px-4 py-3 text-[12.5px]"
      style={{ background: "var(--color-accent-dim)", border: "1px solid var(--color-accent-soft)", color: "var(--color-text-2)" }}
    >
      <Loader2 className="h-4 w-4 shrink-0 motion-safe:animate-spin" style={{ color: "var(--color-accent-2)" }} aria-hidden />
      <span>
        {latestTask?.status === "running" ? t("script_plan_progress_running") : t("script_plan_progress_queued")}
        {" "}
        <span style={{ color: "var(--color-text-4)" }}>{t("script_plan_progress_hint")}</span>
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 可折叠导览区：节拍横排卡 + 尾钩子条
// ---------------------------------------------------------------------------

function GuideSection({ meta }: { meta: EpisodeMeta | undefined }) {
  const { t } = useTranslation("dashboard");
  const [collapsed, setCollapsed] = useState(false);
  const beats = meta?.outline?.story_beats ?? [];
  const hook = meta?.hook;
  if (beats.length === 0 && !hook) return null;

  const summary = [
    beats.length > 0 ? t("episode_workspace_guide_beats", { count: beats.length }) : null,
    hook ? t("episode_workspace_guide_hook") : null,
  ]
    .filter(Boolean)
    .join(" · ");

  // 开关做成容器卡的 header 行：折叠时整卡收成一行，展开时内容都在同一个框内，
  // 「开关控制的是这个框」的对应关系可见
  return (
    <section
      className="mt-4 overflow-hidden rounded-xl"
      style={{ background: "oklch(0.21 0.012 265 / 0.35)", border: "1px solid var(--color-hairline)" }}
    >
      <button
        type="button"
        onClick={() => setCollapsed((v) => !v)}
        aria-expanded={!collapsed}
        className="focus-ring flex w-full items-center gap-2 px-4 py-2.5 text-left text-[11.5px] font-semibold tracking-wide transition-colors hover:bg-[oklch(1_0_0_/_0.03)]"
        style={{
          color: "var(--color-text-3)",
          borderBottom: collapsed ? "none" : "1px solid var(--color-hairline-soft)",
        }}
      >
        <ChevronDown
          className={`h-3.5 w-3.5 shrink-0 transition-transform ${collapsed ? "-rotate-90" : ""}`}
          aria-hidden
        />
        {t("episode_workspace_guide_title")}
        <span className="font-normal" style={{ color: "var(--color-text-4)" }}>
          {summary}
        </span>
        <span className="ml-auto shrink-0 font-normal" style={{ color: "var(--color-text-4)" }}>
          {collapsed ? t("episode_workspace_guide_expand") : t("episode_workspace_guide_collapse")}
        </span>
      </button>

      {!collapsed && (
        <div className="space-y-2.5 px-4 pb-4 pt-3">
          {beats.length > 0 ? (
            <div
              className="grid gap-2.5"
              style={{ gridTemplateColumns: `repeat(${Math.min(beats.length, 4)}, 1fr)` }}
            >
              {beats.map((b, i) => (
                <div
                  key={i}
                  className="rounded-lg px-3.5 py-3"
                  style={{ background: "oklch(0.24 0.012 265 / 0.55)", border: "1px solid var(--color-hairline-soft)" }}
                >
                  <span className="num text-[15px] font-bold" style={{ color: "var(--color-accent-2)" }}>
                    {i + 1}
                  </span>
                  <p className="mt-1 text-[12px] leading-[1.6]" style={{ color: "var(--color-text-2)" }}>
                    {b}
                  </p>
                </div>
              ))}
            </div>
          ) : null}

          {hook ? (
            <div
              className="flex items-start gap-2.5 rounded-lg px-3.5 py-3"
              style={{ background: "var(--color-accent-dim)", border: "1px solid var(--color-accent-soft)" }}
            >
              <Anchor className="mt-0.5 h-3.5 w-3.5 shrink-0" style={{ color: "var(--color-accent-2)" }} aria-hidden />
              <p className="text-[12.5px] leading-[1.7]" style={{ color: "var(--color-text-2)" }}>
                <span className="mr-2 font-semibold" style={{ color: "var(--color-accent-2)" }}>
                  {t("episode_workspace_guide_hook")}
                </span>
                {hook}
              </p>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// 集原文填写框：无原文的集填写或粘贴，自带原文的集改写
// ---------------------------------------------------------------------------

function SourceEditor({
  initialText,
  initialKind,
  saving,
  focusToken,
  onSave,
  onCancel,
}: {
  initialText: string;
  /** 源文件类型的初始值；null 时不提供类型选择（非剧情演绎项目）。 */
  initialKind: SourceKind | null;
  saving: boolean;
  /** 每次变化都把焦点移到填写框（制作进度面板的「补充集原文」）。 */
  focusToken: number;
  onSave: (text: string, sourceKind: SourceKind | undefined) => void;
  onCancel: (() => void) | null;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [draft, setDraft] = useState(initialText);
  const [kind, setKind] = useState<SourceKind | null>(initialKind);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const blank = draft.trim() === "";

  useEffect(() => {
    if (focusToken === 0) return;
    textareaRef.current?.focus();
    textareaRef.current?.scrollIntoView({ block: "center" });
  }, [focusToken]);
  return (
    <div className="mx-auto flex h-full max-w-[66ch] flex-col gap-3">
      {onCancel ? null : (
        <p className="text-[13px] leading-[1.7]" style={{ color: "var(--color-text-3)" }}>
          {t("episode_workspace_source_empty_hint")}
        </p>
      )}
      <textarea
        ref={textareaRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder={t("episode_workspace_source_placeholder")}
        aria-label={t("episode_workspace_source_placeholder")}
        disabled={saving}
        className="focus-ring min-h-[280px] flex-1 resize-none rounded-lg px-4 py-3 text-[14px] leading-[1.9]"
        style={{
          color: "var(--color-text-2)",
          background: "oklch(0.18 0.010 265 / 0.6)",
          border: "1px solid var(--color-hairline)",
        }}
      />
      <div className="flex items-center justify-end gap-2">
        {kind !== null ? (
          <label className="mr-auto flex items-center gap-2 text-[12px]" style={{ color: "var(--color-text-3)" }}>
            {t("dashboard:source_kind")}
            <SourceKindSelect
              value={kind}
              onChange={setKind}
              disabled={saving}
              label={t("dashboard:source_kind")}
            />
          </label>
        ) : null}
        {onCancel ? (
          <button
            type="button"
            onClick={onCancel}
            disabled={saving}
            className="focus-ring rounded-lg px-3.5 py-1.5 text-[12.5px]"
            style={{ color: "var(--color-text-3)", border: "1px solid var(--color-hairline)" }}
          >
            {t("common:cancel")}
          </button>
        ) : null}
        <button
          type="button"
          onClick={() => onSave(draft, kind ?? undefined)}
          disabled={saving || blank}
          className="arc-btn-primary focus-ring rounded-lg px-4 py-1.5 text-[12.5px] font-semibold disabled:opacity-50"
        >
          {saving ? t("common:saving") : t("episode_workspace_source_save")}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 入口：顶栏导览（可折叠） + 全宽居中阅读列
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
  const { t } = useTranslation("dashboard");
  // 取到的切片带上归属 key，loading 由 key 是否匹配派生（避免 effect 内同步 setState）
  const [fetched, setFetched] = useState<{ key: string; text: string | null } | null>(null);

  const meta = episodes.find((e) => e.episode === episode);
  const isDrama = useProjectsStore((s) => s.currentProjectData?.content_mode === "drama");

  const origin = sourceOriginOf(meta);
  // 无原文的集没有集原文文件：盘上同名的 episode_N.txt 是未登记文件，不当作本集原文读取
  const withoutSource = meta !== undefined && origin === "none";
  const fetchKey = `${projectName}::${episode}`;
  useEffect(() => {
    if (withoutSource) return;
    let disposed = false;
    void API.getSourceContent(projectName, `episode_${episode}.txt`)
      .catch(() => null)
      .then((text) => {
        if (disposed) return;
        setFetched({ key: `${projectName}::${episode}`, text });
      });
    return () => {
      disposed = true;
    };
  }, [projectName, episode, withoutSource]);

  const loading = !withoutSource && fetched?.key !== fetchKey;
  // 无原文的集只显示本页刚保存的内容（保存后账本刷新前，来源仍是 none）
  const text = !loading && fetched?.key === fetchKey ? fetched.text : null;
  // 编辑态同样带归属 key，切集后自动退出
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const editable = origin !== "whole_source";
  const editing = editable && (editingKey === fetchKey || (!loading && !text));
  const [focusToken, setFocusToken] = useState(0);

  useEpisodeSurfaceRequest(projectName, episode, "episode_source", () => {
    if (!editable) return;
    setEditingKey(fetchKey);
    setFocusToken((value) => value + 1);
  });

  const [pendingSave, setPendingSave] = useState<{
    draft: string;
    sourceKind: SourceKind | undefined;
    episodes: number[];
  } | null>(null);

  const handleSave = useCallback(
    async (draft: string, sourceKind: SourceKind | undefined, confirm = false) => {
      setSaving(true);
      try {
        const result = await API.updateEpisodeSource(projectName, episode, draft, sourceKind, confirm);
        if (result.needs_confirmation) {
          setPendingSave({ draft, sourceKind, episodes: result.affected_episodes });
          return;
        }
        setPendingSave(null);
        setFetched({ key: `${projectName}::${episode}`, text: draft });
        setEditingKey(null);
        useAppStore.getState().pushToast(t("episode_workspace_source_saved"), "success");
        void useProjectsStore.getState().refreshProject(projectName);
      } catch (err) {
        useAppStore.getState().pushToast(t("episode_workspace_source_save_failed", { message: errMsg(err) }), "error");
      } finally {
        setSaving(false);
      }
    },
    [projectName, episode, t],
  );

  const handleSaveTitle = useCallback(
    async (title: string) => {
      try {
        await API.updateEpisode(projectName, episode, { title });
        await useProjectsStore.getState().refreshProject(projectName);
        useAppStore.getState().pushToast(t("episode_title_updated"), "success");
      } catch (err) {
        useAppStore.getState().pushToast(t("episode_title_update_failed", { message: errMsg(err) }), "error");
        throw err;
      }
    },
    [projectName, episode, t],
  );

  return (
    <div className="flex h-full flex-col p-6">
      <div className="mx-auto flex min-h-0 w-full max-w-4xl flex-1 flex-col">
        <EpisodeHeader
          episode={episode}
          episodes={episodes}
          meta={meta}
          onSaveTitle={handleSaveTitle}
          actions={
            <>
              <StartBlankScriptButton
                projectName={projectName}
                episode={episode}
                discardsPlan={false}
                className="focus-ring rounded-lg border border-[var(--color-hairline)] px-4 py-2 text-[12.5px] font-medium text-[var(--color-text-2)] transition-colors hover:text-[var(--color-text)]"
              />
              <ScriptPlanButton
                projectName={projectName}
                episode={episode}
                replaces="none"
                className="arc-btn-primary focus-ring rounded-lg px-4 py-2 text-[12.5px] font-semibold"
              />
            </>
          }
        />
        <ScriptPlanProgress projectName={projectName} episode={episode} />
        <GuideSection key={episode} meta={meta} />

        <div className="mt-4 flex min-h-0 flex-1 flex-col">
          <div
            className="min-h-0 flex-1 overflow-y-auto rounded-2xl px-12 py-9"
            style={{
              background: "linear-gradient(180deg, oklch(0.215 0.011 265 / 0.75), oklch(0.195 0.010 265 / 0.75))",
              border: "1px solid var(--color-hairline)",
              boxShadow: "inset 0 1px 0 oklch(1 0 0 / 0.04)",
            }}
          >
            {loading ? (
              <p className="text-center text-[13px]" style={{ color: "var(--color-text-4)" }}>
                {t("episode_workspace_source_loading")}
              </p>
            ) : editing ? (
              <SourceEditor
                key={fetchKey}
                initialText={text ?? ""}
                initialKind={isDrama ? (meta?.source_kind ?? "novel") : null}
                saving={saving}
                focusToken={focusToken}
                onSave={(draft, sourceKind) => void handleSave(draft, sourceKind)}
                onCancel={text ? () => setEditingKey(null) : null}
              />
            ) : text ? (
              <div className="mx-auto max-w-[66ch] pb-10">
                {editable ? (
                  <div className="mb-3 flex justify-end">
                    <button
                      type="button"
                      onClick={() => setEditingKey(fetchKey)}
                      className="focus-ring inline-flex items-center gap-1.5 rounded-lg px-3 py-1 text-[12px]"
                      style={{ color: "var(--color-text-3)", border: "1px solid var(--color-hairline)" }}
                    >
                      <PencilLine className="h-3.5 w-3.5" aria-hidden />
                      {t("episode_workspace_source_edit")}
                    </button>
                  </div>
                ) : null}
                <p
                  className="whitespace-pre-wrap text-[14px] leading-[2]"
                  style={{ color: "var(--color-text-2)", textAlign: "justify" }}
                >
                  {text}
                </p>
              </div>
            ) : (
              <p className="text-center text-[13px]" style={{ color: "var(--color-text-4)" }}>
                {t("episode_workspace_source_missing")}
              </p>
            )}
          </div>
        </div>
      </div>
      <ConfirmDialog
        open={pendingSave !== null}
        title={t("source_kind_change_episode_title")}
        description={
          <>
            <span className="block">{t("source_kind_change_episode_desc")}</span>
            <ul className="mt-2 list-disc space-y-0.5 pl-5">
              {(pendingSave?.episodes ?? []).map((affected) => (
                <li key={affected}>
                  {t("source_kind_change_episode", {
                    position: episodePosition(episodes, affected) ?? "?",
                    name: episodeDisplayName(episodes, affected, t),
                  })}
                </li>
              ))}
            </ul>
          </>
        }
        confirmLabel={t("source_kind_change_episode_confirm")}
        loading={saving}
        onConfirm={() => {
          if (pendingSave) void handleSave(pendingSave.draft, pendingSave.sourceKind, true);
        }}
        onCancel={() => setPendingSave(null)}
      />
    </div>
  );
}
