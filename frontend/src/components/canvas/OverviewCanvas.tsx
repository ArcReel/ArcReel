
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Pencil, RefreshCw, Sparkles, Users, Landmark, Package } from "lucide-react";
import type { ProjectData } from "@/types";
import { API, ConflictError } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { useCostStore } from "@/stores/cost-store";
import { costEntries, formatCost, totalBreakdown } from "@/utils/cost-format";
import { errMsg } from "@/utils/async";

import { WelcomeCanvas } from "./WelcomeCanvas";
import { AdInitCanvas } from "./AdInitCanvas";
import { ConflictModal, type ConflictResolution } from "./ConflictModal";
import { AgentHandoffHint } from "@/components/copilot/AgentHandoffHint";

interface OverviewCanvasProps {
  projectName: string;
  projectData: ProjectData | null;
}

const CARD_BG =
  "linear-gradient(180deg, oklch(0.22 0.012 265 / 0.55), oklch(0.19 0.010 265 / 0.40))";
const CARD_SHADOW =
  "inset 0 1px 0 oklch(1 0 0 / 0.04), 0 8px 24px -10px oklch(0 0 0 / 0.5)";
const FIELD_STYLE: React.CSSProperties = {
  background:
    "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.6), oklch(0.18 0.010 265 / 0.45))",
  border: "1px solid var(--color-hairline)",
  color: "var(--color-text)",
  boxShadow: "inset 0 1px 2px oklch(0 0 0 / 0.2)",
};

interface OverviewDraft {
  synopsis: string;
  genre: string;
  theme: string;
  world_setting: string;
}

export function OverviewCanvas({ projectName, projectData }: OverviewCanvasProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const tRef = useRef(t);
  tRef.current = t;
  // 广告/短片项目恒单集：界面隐藏「集」语义，区块按单视频呈现
  const isAd = projectData?.content_mode === "ad";
  const projectTotals = useCostStore((s) => s.costData?.project_totals);
  const getEpisodeCost = useCostStore((s) => s.getEpisodeCost);
  const costLoading = useCostStore((s) => s.loading);
  const costError = useCostStore((s) => s.error);
  const debouncedFetch = useCostStore((s) => s.debouncedFetch);

  useEffect(() => {
    if (!projectName) return;
    debouncedFetch(projectName);
  }, [projectName, projectData?.episodes, debouncedFetch]);

  const [regenerating, setRegenerating] = useState(false);
  const [conflictPrompt, setConflictPrompt] = useState<{
    existing: string;
    suggestedName: string;
    resolve: (d: ConflictResolution) => void;
  } | null>(null);

  // 在「欢迎页 → 概览页」首次切换时触发一次智能体引导动画。
  // 仅当本次会话内 showWelcome 由 true 变为 false 才递增 trigger，
  // 加载已有概览的项目不会触发；AgentHandoffHint 内还有 sessionStorage
  // 防 reload 重复。
  const [handoffTrigger, setHandoffTrigger] = useState(0);
  const wasWelcomeRef = useRef<boolean | null>(null);
  useEffect(() => {
    if (!projectData) return;
    const isWelcome = !projectData.overview && (projectData.episodes?.length ?? 0) === 0;
    if (wasWelcomeRef.current === true && !isWelcome) {
      setHandoffTrigger((k) => k + 1);
    }
    wasWelcomeRef.current = isWelcome;
  }, [projectData]);

  const refreshProject = useCallback(
    async () => {
      const res = await API.getProject(projectName);
      useProjectsStore.getState().setCurrentProject(
        projectName,
        res.project,
        res.scripts ?? {},
        res.asset_fingerprints,
      );
    },
    [projectName],
  );

  const handleUpload = useCallback(
    async (file: File) => {
      const tryUpload = async (
        onConflict?: "fail" | "replace" | "rename"
      ): Promise<void> => {
        const res = await API.uploadFile(projectName, "source", file, null, {
          onConflict,
        });
        const filename = res.filename ?? file.name;
        const enc = res.used_encoding ?? null;
        const chapters = res.chapter_count ?? 0;
        const hasEncoding = enc !== null;
        let key: string;
        if (hasEncoding && chapters > 0) {
          key = "source_normalized_toast_with_chapters";
        } else if (hasEncoding) {
          key = "source_normalized_toast";
        } else if (chapters > 0) {
          key = "source_normalized_toast_native_with_chapters";
        } else {
          key = "source_normalized_toast_native";
        }
        useAppStore
          .getState()
          .pushToast(
            tRef.current(key, { filename, encoding: enc, chapters }),
            "success",
          );
      };

      try {
        await tryUpload();
      } catch (err) {
        if (err instanceof ConflictError) {
          const decision = await new Promise<ConflictResolution>((resolve) => {
            setConflictPrompt({
              existing: err.existing,
              suggestedName: err.suggestedName,
              resolve,
            });
          });
          setConflictPrompt(null);
          if (decision === "cancel") return;
          await tryUpload(decision);
        } else {
          throw err;
        }
      }
    },
    [projectName],
  );

  const handleAnalyze = useCallback(async () => {
    await API.generateOverview(projectName);
    await refreshProject();
  }, [projectName, refreshProject]);

  const handleRegenerate = useCallback(async () => {
    setRegenerating(true);
    try {
      await API.generateOverview(projectName);
      await refreshProject();
      useAppStore.getState().pushToast(tRef.current("project_overview_regenerated"), "success");
    } catch (err) {
      useAppStore
        .getState()
        .pushNotification(tRef.current("regenerate_failed", { message: errMsg(err) }), "error");
    } finally {
      setRegenerating(false);
    }
  }, [projectName, refreshProject]);

  const [editingOverview, setEditingOverview] = useState(false);
  const [savingOverview, setSavingOverview] = useState(false);
  const [draft, setDraft] = useState<OverviewDraft>({
    synopsis: "",
    genre: "",
    theme: "",
    world_setting: "",
  });
  const synopsisFieldId = useId();
  const genreFieldId = useId();
  const themeFieldId = useId();
  const worldFieldId = useId();

  const enterOverviewEdit = useCallback(() => {
    const ov = projectData?.overview;
    setDraft({
      synopsis: ov?.synopsis ?? "",
      genre: ov?.genre ?? "",
      theme: ov?.theme ?? "",
      world_setting: ov?.world_setting ?? "",
    });
    setEditingOverview(true);
  }, [projectData]);

  const handleSaveOverview = useCallback(async () => {
    setSavingOverview(true);
    try {
      // 与分集标题写入口一致:落盘前裁剪首尾空白(避免持久化纯空白/缩进噪音)
      await API.updateOverview(projectName, {
        synopsis: draft.synopsis.trim(),
        genre: draft.genre.trim(),
        theme: draft.theme.trim(),
        world_setting: draft.world_setting.trim(),
      });
      await refreshProject();
      setEditingOverview(false);
      useAppStore.getState().pushToast(tRef.current("overview_updated"), "success");
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(tRef.current("update_overview_failed", { message: errMsg(err) }), "error");
    } finally {
      setSavingOverview(false);
    }
  }, [projectName, draft, refreshProject]);

  if (!projectData) {
    // 项目数据加载期间保留同结构的空容器（不渲染居中 loading 文字），
    // 避免「居中提示 → 顶端内容」的位置跳跃造成闪动；StudioLayout 的外壳
    // 仍然可见，加载完成后内容平滑出现。
    return <div className="h-full overflow-y-auto" aria-busy="true" />;
  }

  const status = projectData.status;
  const overview = projectData.overview;
  const showWelcome = !overview && (projectData.episodes?.length ?? 0) === 0;
  // ad 项目恒单集（episodes 非空），不会落入 showWelcome；建项后素材全空时进入初始化页：
  // 上传产品图 + 产品描述 + brief + 可选 sheet 生成。任一素材就绪即切回概览。
  const showAdInit =
    isAd &&
    Object.keys(projectData.products ?? {}).length === 0 &&
    !(projectData.brief ?? "").trim();

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-5 px-6 py-6">
        {/* Project title — display-serif heading with accent dash */}
        <header className="flex items-end gap-3">
          <span
            aria-hidden
            className="mb-1 h-6 w-[3px] rounded-full"
            style={{
              background:
                "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))",
              boxShadow: "0 0 12px var(--color-accent-glow)",
            }}
          />
          <div>
            <h1
              className="display-serif text-[28px] font-semibold tracking-tight"
              style={{ color: "var(--color-text)" }}
            >
              {projectData.title}
            </h1>
            <p
              className="num mt-0.5 text-[10.5px] uppercase"
              style={{
                color: "var(--color-text-4)",
                letterSpacing: "1.4px",
              }}
            >
              {projectData.content_mode === "narration"
                ? t("narration_visuals_mode")
                : projectData.content_mode === "ad"
                  ? t("ad_short_video_mode")
                  : t("drama_animation_mode")}
            </p>
          </div>
        </header>

        {showAdInit ? (
          <AdInitCanvas projectName={projectName} onDone={refreshProject} />
        ) : showWelcome ? (
          <WelcomeCanvas
            projectName={projectName}
            projectTitle={projectData.title}
            onUpload={handleUpload}
            onAnalyze={handleAnalyze}
          />
        ) : (
          <>
            {/* Synopsis / overview card */}
            <section
              className="relative overflow-hidden rounded-2xl p-5"
              style={{
                border: "1px solid var(--color-hairline-soft)",
                background: CARD_BG,
                boxShadow: CARD_SHADOW,
              }}
            >
              <span
                aria-hidden
                className="pointer-events-none absolute inset-x-0 top-0 h-px"
                style={{
                  background:
                    "linear-gradient(90deg, transparent, var(--color-accent-soft), transparent)",
                }}
              />
              <div className="mb-3 flex items-center gap-2.5">
                <Sparkles className="h-3.5 w-3.5" style={{ color: "var(--color-accent-2)" }} />
                <span
                  className="text-[10.5px] font-bold uppercase"
                  style={{ color: "var(--color-text-4)", letterSpacing: "1.0px" }}
                >
                  {t("project_overview_title")}
                </span>
                <div className="flex-1" />
                {!editingOverview && (
                  <>
                    <button
                      type="button"
                      onClick={enterOverviewEdit}
                      title={t("edit_overview")}
                      className="focus-ring inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-[var(--color-text-3)] transition-colors hover:bg-[oklch(1_0_0_/_0.05)] hover:text-[var(--color-text)]"
                    >
                      <Pencil className="h-3 w-3" />
                      <span>{t("edit_overview")}</span>
                    </button>
                    {overview && (
                      <button
                        type="button"
                        onClick={() => void handleRegenerate()}
                        disabled={regenerating}
                        title={t("regen_overview_title")}
                        className="focus-ring inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-[var(--color-text-3)] transition-colors hover:bg-[oklch(1_0_0_/_0.05)] hover:text-[var(--color-text)] disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent disabled:hover:text-[var(--color-text-3)]"
                      >
                        <RefreshCw className={`h-3 w-3 ${regenerating ? "animate-spin" : ""}`} />
                        <span>{regenerating ? t("regenerating_short") : t("regen_short")}</span>
                      </button>
                    )}
                  </>
                )}
              </div>

              {editingOverview ? (
                <div className="space-y-3">
                  <div>
                    <FieldLabel htmlFor={synopsisFieldId}>{t("synopsis_label")}</FieldLabel>
                    <textarea
                      id={synopsisFieldId}
                      value={draft.synopsis}
                      onChange={(e) => setDraft((d) => ({ ...d, synopsis: e.target.value }))}
                      disabled={savingOverview}
                      rows={4}
                      className="focus-ring mt-1.5 w-full resize-y rounded-lg px-3 py-2 text-[13px] leading-[1.6] outline-none"
                      style={FIELD_STYLE}
                      placeholder={t("synopsis_placeholder")}
                    />
                  </div>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <div>
                      <FieldLabel htmlFor={genreFieldId}>{t("genre_label")}</FieldLabel>
                      <input
                        id={genreFieldId}
                        type="text"
                        value={draft.genre}
                        onChange={(e) => setDraft((d) => ({ ...d, genre: e.target.value }))}
                        disabled={savingOverview}
                        className="focus-ring mt-1.5 w-full rounded-lg px-3 py-2 text-[13px] outline-none"
                        style={FIELD_STYLE}
                      />
                    </div>
                    <div>
                      <FieldLabel htmlFor={themeFieldId}>{t("theme_label")}</FieldLabel>
                      <input
                        id={themeFieldId}
                        type="text"
                        value={draft.theme}
                        onChange={(e) => setDraft((d) => ({ ...d, theme: e.target.value }))}
                        disabled={savingOverview}
                        className="focus-ring mt-1.5 w-full rounded-lg px-3 py-2 text-[13px] outline-none"
                        style={FIELD_STYLE}
                      />
                    </div>
                  </div>
                  <div>
                    <FieldLabel htmlFor={worldFieldId}>{t("world_setting_label")}</FieldLabel>
                    <textarea
                      id={worldFieldId}
                      value={draft.world_setting}
                      onChange={(e) => setDraft((d) => ({ ...d, world_setting: e.target.value }))}
                      disabled={savingOverview}
                      rows={3}
                      className="focus-ring mt-1.5 w-full resize-y rounded-lg px-3 py-2 text-[13px] leading-[1.6] outline-none"
                      style={FIELD_STYLE}
                    />
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => void handleSaveOverview()}
                      disabled={savingOverview}
                      className="focus-ring inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12px] font-medium transition-transform disabled:cursor-not-allowed disabled:opacity-50"
                      style={{
                        color: "oklch(0.14 0 0)",
                        background:
                          "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
                        boxShadow:
                          "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 6px 18px -4px var(--color-accent-glow), 0 0 0 1px var(--color-accent-soft)",
                      }}
                    >
                      {savingOverview ? t("common:saving") : t("common:save")}
                    </button>
                    <button
                      type="button"
                      onClick={() => setEditingOverview(false)}
                      disabled={savingOverview}
                      className="focus-ring rounded-md px-3 py-1.5 text-[12px] text-[var(--color-text-3)] transition-colors hover:text-[var(--color-text)] disabled:opacity-50"
                    >
                      {t("common:cancel")}
                    </button>
                  </div>
                </div>
              ) : overview ? (
                <>
                  {overview.synopsis && (
                    <p
                      className="text-[13px] leading-[1.7]"
                      style={{ color: "var(--color-text-2)" }}
                    >
                      {overview.synopsis}
                    </p>
                  )}
                  <div
                    className="mt-3.5 flex flex-wrap gap-2 text-[11px]"
                    style={{ color: "var(--color-text-4)" }}
                  >
                    {overview.genre && (
                      <span
                        className="inline-flex items-center gap-1.5 rounded-md px-2 py-0.5"
                        style={{
                          background: "var(--color-accent-dim)",
                          border: "1px solid var(--color-accent-soft)",
                          color: "var(--color-accent-2)",
                        }}
                      >
                        <span style={{ color: "var(--color-text-4)" }}>{t("genre_prefix")}</span>
                        {overview.genre}
                      </span>
                    )}
                    {overview.theme && (
                      <span
                        className="inline-flex items-center gap-1.5 rounded-md px-2 py-0.5"
                        style={{
                          background: "oklch(0.20 0.011 265 / 0.6)",
                          border: "1px solid var(--color-hairline)",
                        }}
                      >
                        <span style={{ color: "var(--color-text-4)" }}>{t("theme_prefix")}</span>
                        <span style={{ color: "var(--color-text-2)" }}>{overview.theme}</span>
                      </span>
                    )}
                  </div>
                  {overview.world_setting && (
                    <div className="mt-3.5">
                      <div
                        className="text-[10px] font-semibold uppercase tracking-[0.12em]"
                        style={{ color: "var(--color-text-4)" }}
                      >
                        {t("world_setting_label")}
                      </div>
                      <p
                        className="mt-1 text-[13px] leading-[1.7]"
                        style={{ color: "var(--color-text-2)" }}
                      >
                        {overview.world_setting}
                      </p>
                    </div>
                  )}
                </>
              ) : (
                <button
                  type="button"
                  onClick={enterOverviewEdit}
                  className="focus-ring flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-[var(--color-hairline)] px-3 py-4 text-[13px] text-[var(--color-text-4)] transition-colors hover:border-[var(--color-accent-soft)] hover:text-[var(--color-text-2)]"
                  style={{ background: "oklch(0.18 0.010 265 / 0.35)" }}
                >
                  <Pencil className="h-4 w-4" />
                  {t("create_overview")}
                </button>
              )}
            </section>

            {/* Asset progress — characters / scenes / props */}
            {status && (
              <section className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                {(["characters", "scenes", "props"] as const).map((key) => {
                  const cat = status[key] as
                    | { total: number; completed: number }
                    | undefined;
                  if (!cat) return null;
                  const pct = cat.total > 0 ? Math.round((cat.completed / cat.total) * 100) : 0;
                  const labels: Record<string, string> = {
                    characters: t("characters"),
                    scenes: t("scenes"),
                    props: t("props"),
                  };
                  const Icon =
                    key === "characters" ? Users : key === "scenes" ? Landmark : Package;
                  return (
                    <div
                      key={key}
                      className="rounded-2xl p-4"
                      style={{
                        border: "1px solid var(--color-hairline-soft)",
                        background: CARD_BG,
                        boxShadow: CARD_SHADOW,
                      }}
                    >
                      <div className="mb-2.5 flex items-center gap-2">
                        <span
                          aria-hidden
                          className="grid h-6 w-6 place-items-center rounded-md"
                          style={{
                            background: "var(--color-accent-dim)",
                            border: "1px solid var(--color-accent-soft)",
                            color: "var(--color-accent-2)",
                          }}
                        >
                          <Icon className="h-3 w-3" />
                        </span>
                        <span
                          className="text-[10.5px] font-bold uppercase"
                          style={{
                            color: "var(--color-text-4)",
                            letterSpacing: "0.8px",
                          }}
                        >
                          {labels[key]}
                        </span>
                        <div className="flex-1" />
                        <span
                          className="num text-[11px]"
                          style={{ color: "var(--color-text-2)" }}
                        >
                          {cat.completed}
                          <span style={{ color: "var(--color-text-4)" }}>/{cat.total}</span>
                        </span>
                      </div>
                      <div
                        className="relative h-1.5 overflow-hidden rounded-full"
                        style={{ background: "oklch(0.16 0.010 265 / 0.7)" }}
                        role="progressbar"
                        aria-label={t("progress_aria_label", { label: labels[key] })}
                        aria-valuenow={pct}
                        aria-valuemin={0}
                        aria-valuemax={100}
                      >
                        <div
                          className="h-full rounded-full transition-all"
                          style={{
                            width: `${pct}%`,
                            background:
                              "linear-gradient(90deg, var(--color-accent), var(--color-accent-2))",
                            boxShadow: "0 0 8px var(--color-accent-glow)",
                          }}
                        />
                      </div>
                      <div
                        className="num mt-1.5 text-right text-[10px]"
                        style={{ color: "var(--color-text-4)" }}
                      >
                        {pct}%
                      </div>
                    </div>
                  );
                })}
              </section>
            )}

            {/* Cost loading / error */}
            {costLoading && (
              <div
                role="status"
                aria-live="polite"
                className="rounded-2xl px-5 py-3 text-[12px] animate-pulse"
                style={{
                  border: "1px solid var(--color-hairline-soft)",
                  background: CARD_BG,
                  color: "var(--color-text-4)",
                }}
              >
                {t("calculating_cost")}
              </div>
            )}
            {costError && (
              <div
                role="alert"
                className="rounded-2xl px-5 py-3 text-[12px]"
                style={{
                  border: "1px solid oklch(0.45 0.18 25 / 0.4)",
                  background: "oklch(0.30 0.10 25 / 0.18)",
                  color: "oklch(0.85 0.10 25)",
                }}
              >
                {t("cost_estimate_failed", { message: costError })}
              </div>
            )}

            {/* Project total cost */}
            {projectTotals && (
              <section
                className="relative overflow-hidden rounded-2xl p-5"
                style={{
                  border: "1px solid var(--color-hairline-soft)",
                  background: CARD_BG,
                  boxShadow: CARD_SHADOW,
                }}
              >
                <div className="mb-3 flex items-center gap-2">
                  <span
                    className="text-[10.5px] font-bold uppercase"
                    style={{
                      color: "var(--color-text-4)",
                      letterSpacing: "1.0px",
                    }}
                  >
                    {t("project_total_cost")}
                  </span>
                </div>
                <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
                  <CostColumn
                    label={t("estimate")}
                    rows={[
                      { label: t("storyboard"), value: formatCost(projectTotals.estimate.image) },
                      { label: t("video"), value: formatCost(projectTotals.estimate.video) },
                      ...(costEntries(projectTotals.estimate.audio).length > 0
                        ? [{ label: t("media_narration_title"), value: formatCost(projectTotals.estimate.audio) }]
                        : []),
                    ]}
                    total={formatCost(totalBreakdown(projectTotals.estimate))}
                    totalLabel={t("cost_total_short")}
                    accent="warm"
                  />
                  <CostColumn
                    label={t("actual")}
                    rows={[
                      { label: t("storyboard"), value: formatCost(projectTotals.actual.image) },
                      { label: t("video"), value: formatCost(projectTotals.actual.video) },
                      ...(costEntries(projectTotals.actual.audio).length > 0
                        ? [{ label: t("media_narration_title"), value: formatCost(projectTotals.actual.audio) }]
                        : []),
                      ...(["characters", "scenes", "props", "products"] as const)
                        .map((kind) => {
                          const bucket = projectTotals.actual[kind];
                          if (bucket == null) return null;
                          return {
                            label: t(`actual_${kind}`),
                            value: formatCost(bucket),
                          };
                        })
                        .filter((r): r is { label: string; value: string } => r !== null),
                    ]}
                    total={formatCost(totalBreakdown(projectTotals.actual))}
                    totalLabel={t("cost_total_short")}
                    accent="good"
                  />
                </div>
              </section>
            )}

            {/* Episodes */}
            <section>
              <div className="mb-2.5 flex items-center gap-2">
                <span
                  aria-hidden
                  className="h-3 w-[3px] rounded-full"
                  style={{
                    background:
                      "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))",
                  }}
                />
                <h3
                  className="display-serif text-[15px] font-semibold tracking-tight"
                  style={{ color: "var(--color-text)" }}
                >
                  {isAd ? t("ad_video_section_title") : t("episodes_title")}
                </h3>
                {!isAd && (projectData.episodes?.length ?? 0) > 0 && (
                  <span
                    className="num text-[10.5px]"
                    style={{ color: "var(--color-text-4)" }}
                  >
                    {projectData.episodes?.length ?? 0}
                  </span>
                )}
              </div>

              {(projectData.episodes?.length ?? 0) === 0 ? (
                <p className="text-[12px]" style={{ color: "var(--color-text-4)" }}>
                  {t("no_episodes_ai_hint")}
                </p>
              ) : (
                <div className="space-y-2">
                  {(projectData.episodes ?? []).map((ep) => {
                    const epCost = getEpisodeCost(ep.episode);
                    return (
                      <div
                        key={ep.episode}
                        className="num flex flex-wrap items-center gap-3 rounded-xl px-4 py-2.5 text-[12px]"
                        style={{
                          border: "1px solid var(--color-hairline-soft)",
                          background:
                            "linear-gradient(180deg, oklch(0.21 0.011 265 / 0.5), oklch(0.18 0.010 265 / 0.35))",
                          boxShadow: "inset 0 1px 0 oklch(1 0 0 / 0.03)",
                        }}
                      >
                        {!isAd && (
                          <span
                            className="rounded px-1.5 py-0.5 text-[10.5px] font-bold"
                            style={{
                              color: "var(--color-accent-2)",
                              background: "var(--color-accent-dim)",
                              border: "1px solid var(--color-accent-soft)",
                            }}
                          >
                            E{ep.episode}
                          </span>
                        )}
                        <span style={{ color: "var(--color-text)", fontFamily: "var(--font-sans)" }}>
                          {ep.title || (isAd ? projectData.title : "")}
                        </span>
                        <span style={{ color: "var(--color-text-4)" }}>
                          {t(isAd ? "shots_and_status" : "segments_and_status", {
                            count: ep.scenes_count ?? "?",
                            status: t(`episode_status_label_${ep.status ?? "draft"}`),
                          })}
                        </span>
                        {epCost && (
                          <span className="ml-auto flex min-w-0 flex-shrink flex-wrap gap-3 text-[11px]">
                            <CostInline
                              label={t("estimate")}
                              imageLabel={t("storyboard")}
                              imageValue={formatCost(epCost.totals.estimate.image)}
                              videoLabel={t("video")}
                              videoValue={formatCost(epCost.totals.estimate.video)}
                              audioLabel={t("media_narration_title")}
                              audioValue={
                                costEntries(epCost.totals.estimate.audio).length > 0
                                  ? formatCost(epCost.totals.estimate.audio)
                                  : undefined
                              }
                              total={formatCost(totalBreakdown(epCost.totals.estimate))}
                              totalLabel={t("total")}
                              accent="warm"
                            />
                            <span style={{ color: "var(--color-hairline-strong)" }}>|</span>
                            <CostInline
                              label={t("actual")}
                              imageLabel={t("storyboard")}
                              imageValue={formatCost(epCost.totals.actual.image)}
                              videoLabel={t("video")}
                              videoValue={formatCost(epCost.totals.actual.video)}
                              audioLabel={t("media_narration_title")}
                              audioValue={
                                costEntries(epCost.totals.actual.audio).length > 0
                                  ? formatCost(epCost.totals.actual.audio)
                                  : undefined
                              }
                              total={formatCost(totalBreakdown(epCost.totals.actual))}
                              totalLabel={t("total")}
                              accent="good"
                            />
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </section>
          </>
        )}

        <div className="h-6" />
      </div>
      {conflictPrompt && (
        <ConflictModal
          existing={conflictPrompt.existing}
          suggestedName={conflictPrompt.suggestedName}
          onResolve={conflictPrompt.resolve}
        />
      )}
      <AgentHandoffHint triggerKey={handoffTrigger} storageScope={projectName} />
    </div>
  );
}

function FieldLabel({
  children,
  htmlFor,
}: {
  children: React.ReactNode;
  htmlFor: string;
}) {
  return (
    <label
      htmlFor={htmlFor}
      className="text-[10px] font-semibold uppercase tracking-[0.12em]"
      style={{ color: "var(--color-text-4)" }}
    >
      {children}
    </label>
  );
}

function CostColumn({
  label,
  rows,
  total,
  totalLabel,
  accent,
}: {
  label: string;
  rows: { label: string; value: string }[];
  total: string;
  totalLabel: string;
  accent: "warm" | "good";
}) {
  const accentColor =
    accent === "warm" ? "oklch(0.85 0.13 75)" : "var(--color-good)";
  return (
    <div>
      <div
        className="mb-1.5 text-[10.5px] uppercase"
        style={{ color: "var(--color-text-4)", letterSpacing: "1.0px" }}
      >
        {label}
      </div>
      <dl className="num space-y-1 text-[12px]">
        {rows.map((row) => (
          <div key={row.label} className="flex items-baseline gap-2">
            <dt
              className="shrink-0 text-[11px]"
              style={{ color: "var(--color-text-4)" }}
            >
              {row.label}
            </dt>
            <dd
              className="flex-1 text-right"
              style={{ color: "var(--color-text-2)" }}
            >
              {row.value}
            </dd>
          </div>
        ))}
        <div
          className="mt-2 flex items-baseline gap-2 border-t pt-2"
          style={{ borderColor: "var(--color-hairline-soft)" }}
        >
          <dt
            className="shrink-0 text-[10.5px] uppercase"
            style={{
              color: "var(--color-text-4)",
              letterSpacing: "0.8px",
            }}
          >
            {totalLabel}
          </dt>
          <dd
            className="flex-1 text-right text-[14px] font-semibold"
            style={{ color: accentColor }}
          >
            {total}
          </dd>
        </div>
      </dl>
    </div>
  );
}

function CostInline({
  label,
  imageLabel,
  imageValue,
  videoLabel,
  videoValue,
  audioLabel,
  audioValue,
  total,
  totalLabel,
  accent,
}: {
  label: string;
  imageLabel: string;
  imageValue: string;
  videoLabel: string;
  videoValue: string;
  audioLabel?: string;
  audioValue?: string;
  total: string;
  totalLabel: string;
  accent: "warm" | "good";
}) {
  const accentColor =
    accent === "warm" ? "oklch(0.85 0.13 75)" : "var(--color-good)";
  return (
    <span>
      <span style={{ color: "var(--color-text-4)" }}>{label} </span>
      <span style={{ color: "var(--color-text-4)" }}>{imageLabel} </span>
      <span style={{ color: "var(--color-text-2)" }}>{imageValue}</span>
      <span className="ml-2" style={{ color: "var(--color-text-4)" }}>
        {videoLabel}{" "}
      </span>
      <span style={{ color: "var(--color-text-2)" }}>{videoValue}</span>
      {audioLabel != null && audioValue != null && (
        <>
          <span className="ml-2" style={{ color: "var(--color-text-4)" }}>
            {audioLabel}{" "}
          </span>
          <span style={{ color: "var(--color-text-2)" }}>{audioValue}</span>
        </>
      )}
      <span className="ml-2" style={{ color: "var(--color-text-4)" }}>
        {totalLabel}{" "}
      </span>
      <span className="font-semibold" style={{ color: accentColor }}>
        {total}
      </span>
    </span>
  );
}
