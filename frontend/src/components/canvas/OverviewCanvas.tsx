
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Pencil, RefreshCw, Sparkles, Users, Landmark, Package } from "lucide-react";
import type { ProjectData } from "@/types";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { useCostStore } from "@/stores/cost-store";
import { costEntries, formatCost, totalBreakdown } from "@/utils/cost-format";
import { errMsg } from "@/utils/async";
import { outputTruncationOfError, type OutputTruncation } from "@/utils/output-truncation";
import { itemCountKey, normalizeRoute } from "@/utils/generation-mode";

import { WelcomeCanvas } from "./WelcomeCanvas";
import { AdInitCanvas } from "./AdInitCanvas";
import { AdBriefCard } from "./AdBriefCard";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { AgentHandoffHint } from "@/components/copilot/AgentHandoffHint";
import { OutputTruncationHint } from "@/components/shared/OutputTruncationHint";
import { ONBOARDING_ANCHORS } from "@/onboarding/anchors";

interface OverviewCanvasProps {
  projectName: string;
  projectData: ProjectData | null;
  /** 只读展示（引导演示项目）：不渲染编辑与重新生成入口。 */
  readOnly?: boolean;
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

export function OverviewCanvas({
  projectName,
  projectData,
  readOnly = false,
}: OverviewCanvasProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  // 广告/短片项目恒单集：界面隐藏「集」语义，区块按单视频呈现
  const isAd = projectData?.content_mode === "ad";
  // 内容规模的口径按生成模式定，与创作类型无关：分镜图生视频报分镜数、参考生视频报视频单元数。
  const route = normalizeRoute(projectData?.generation_mode);
  const projectTotals = useCostStore((s) => s.costData?.project_totals);
  const getEpisodeCost = useCostStore((s) => s.getEpisodeCost);
  const costLoading = useCostStore((s) => s.loading);
  const costError = useCostStore((s) => s.error);
  const debouncedFetch = useCostStore((s) => s.debouncedFetch);

  useEffect(() => {
    // 演示项目的费用请求交给费用 store 自身的 isDemoProject 分支跳过并失效：
    // 那条分支会取消已排队的防抖计时器、abort 在途的真实项目请求、清空费用状态——
    // 这里若改成对 readOnly 直接 return，反而会绕过这套失效机制，让切入只读态前
    // 真实项目已排队的防抖任务照常在之后触发，把真实费用写回全局 store。
    if (!projectName) return;
    debouncedFetch(projectName);
  }, [projectName, projectData?.episodes, debouncedFetch]);

  const [regenerating, setRegenerating] = useState(false);
  // 重新生成因输出被截断而失败时的出路（登记最大输出长度或换模型）；toast 会消失，出路留在概述卡上直到下次重试。
  const [regenerateTruncation, setRegenerateTruncation] = useState<OutputTruncation | null>(null);
  const [confirmingRegenerate, setConfirmingRegenerate] = useState(false);

  // 欢迎页的上传对话框打开期间欢迎页保持挂载：逐集原文登记出第一集后项目就不再是空项目，
  // 此时卸载欢迎页会连带卸载对话框、中止剩下的上传。按项目名记录，切项目后不沿用。
  const [welcomeUpload, setWelcomeUpload] = useState<{ projectName: string; files: File[] } | null>(null);
  const welcomeUploadFiles = welcomeUpload?.projectName === projectName ? welcomeUpload.files : null;
  const welcomeUploadOpen = welcomeUploadFiles !== null;
  const setWelcomeUploadFiles = useCallback(
    (files: File[] | null) => setWelcomeUpload(files === null ? null : { projectName, files }),
    [projectName],
  );

  // 在「欢迎页 → 概览页」首次切换时触发一次 Agent 引导动画。
  // 仅当本次会话内 showWelcome 由 true 变为 false 才递增 trigger，
  // 加载已有概览的项目不会触发；AgentHandoffHint 内还有 sessionStorage
  // 防 reload 重复。
  const [handoffTrigger, setHandoffTrigger] = useState(0);
  const wasWelcomeRef = useRef<boolean | null>(null);
  useEffect(() => {
    // 只读态（如切入演示项目）不触发交接提示：同一路由复用同一个 OverviewCanvas 实例时，
    // 上一个真实项目若停在欢迎页，演示数据的 overview/episodes 一到位就会被误判成
    // 「欢迎页 → 完成」，提示会强行打开 Agent 面板——但演示态已卸载该面板，
    // 离开演示项目后还会带出一个意外展开的 Agent。清空 ref 避免下次真实切换沿用这份脏状态。
    // trigger 一并归零：AgentHandoffHint 按 `<storageScope>:<triggerKey>` 去重，切项目后
    // 同一个非零 trigger 会被当成新项目的新事件。若不清零，「项目 A 完成交接 → 途经演示
    // 项目 → 进入项目 B」会让 B 凭 A 留下的 trigger 误弹提示并强行展开 Agent，而 B 根本没
    // 发生过「欢迎页 → 完成」转换。只读态下 AgentHandoffHint 不渲染，此处置 0 不会与它的
    // effect 抢同一次提交。
    if (readOnly) {
      wasWelcomeRef.current = null;
      // eslint-disable-next-line react-hooks/set-state-in-effect -- 切入只读态时清零交接提示的 trigger，是有意的状态重置
      setHandoffTrigger(0);
      return;
    }
    if (!projectData) return;
    const isWelcome = welcomeUploadOpen || (!projectData.overview && (projectData.episodes?.length ?? 0) === 0);
    if (wasWelcomeRef.current === true && !isWelcome) {
      setHandoffTrigger((k) => k + 1);
    }
    wasWelcomeRef.current = isWelcome;
  }, [projectData, readOnly, welcomeUploadOpen]);

  // 在途合并 + 失败留旧收敛于 projects-store；此处仅表达刷新意图，忽略返回值。
  const refreshProject = useCallback(
    async () => {
      await useProjectsStore.getState().refreshProject(projectName);
    },
    [projectName],
  );

  const handleAnalyze = useCallback(async () => {
    await API.generateOverview(projectName);
    await refreshProject();
  }, [projectName, refreshProject]);

  const handleRegenerate = useCallback(async () => {
    setRegenerating(true);
    setRegenerateTruncation(null);
    try {
      await API.generateOverview(projectName);
      await refreshProject();
      useAppStore.getState().pushToast(t("project_overview_regenerated"), "success");
    } catch (err) {
      setRegenerateTruncation(outputTruncationOfError(err));
      useAppStore
        .getState()
        .pushNotification(t("regenerate_failed", { message: errMsg(err) }), "error");
    } finally {
      setRegenerating(false);
      setConfirmingRegenerate(false);
    }
  }, [projectName, refreshProject, t]);

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

  // 编辑态期间切到只读项目（如切入演示项目）时立即退出编辑态，避免表单和保存操作残留可用。
  useEffect(() => {
    if (!readOnly) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 切入只读态时退出编辑态，是有意的 UI 状态重置
    setEditingOverview(false);
  }, [readOnly]);

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
      useAppStore.getState().pushToast(t("overview_updated"), "success");
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(t("update_overview_failed", { message: errMsg(err) }), "error");
    } finally {
      setSavingOverview(false);
    }
  }, [projectName, draft, refreshProject, t]);

  if (!projectData) {
    // 项目数据加载期间保留同结构的空容器（不渲染居中 loading 文字），
    // 避免「居中提示 → 顶端内容」的位置跳跃造成闪动；StudioLayout 的外壳
    // 仍然可见，加载完成后内容平滑出现。
    return <div className="h-full overflow-y-auto" aria-busy="true" />;
  }

  const status = projectData.status;
  const overview = projectData.overview;
  const showWelcome = welcomeUploadOpen || (!overview && (projectData.episodes?.length ?? 0) === 0);
  // ad 项目恒单集（episodes 非空），不会落入 showWelcome；建项后素材全空时进入初始化页：
  // 上传商品图 + 商品描述 + brief + 可选 sheet 生成。任一素材就绪即切回概览。
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
            key={projectName}
            projectName={projectName}
            projectTitle={projectData.title}
            wholeSourceFiles={(projectData.whole_source_files ?? []).map((item) => item.source_file)}
            uploadFiles={welcomeUploadFiles}
            onUploadFilesChange={setWelcomeUploadFiles}
            onAnalyze={handleAnalyze}
          />
        ) : (
          <>
            {isAd && (
              <AdBriefCard
                projectName={projectName}
                brief={projectData.brief ?? ""}
                targetDuration={projectData.target_duration}
                readOnly={readOnly}
                onSaved={refreshProject}
              />
            )}
            {/* Synopsis / overview card */}
            <section
              data-onboarding={ONBOARDING_ANCHORS.workbenchOverview}
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
                {!editingOverview && !readOnly && (
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
                        onClick={() => setConfirmingRegenerate(true)}
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

              {regenerateTruncation && !readOnly ? (
                <div role="alert" className="mb-3 text-[12px]">
                  <OutputTruncationHint truncation={regenerateTruncation} />
                </div>
              ) : null}

              {editingOverview && !readOnly ? (
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
              ) : readOnly ? null : (
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
                {(["character", "scene", "prop"] as const).map((key) => {
                  const cat = status.assets?.[key];
                  if (!cat) return null;
                  const pct = cat.total > 0 ? Math.round((cat.available / cat.total) * 100) : 0;
                  const labels: Record<string, string> = {
                    character: t("characters"),
                    scene: t("scenes"),
                    prop: t("props"),
                  };
                  const Icon = key === "character" ? Users : key === "scene" ? Landmark : Package;
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
                          {cat.available}
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
            {!readOnly && costLoading && (
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
            {!readOnly && costError && (
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
            {!readOnly && projectTotals && (
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
                      ...(costEntries(projectTotals.actual.unassigned).length > 0
                        ? [
                            {
                              label: t("actual_unassigned_history"),
                              value: formatCost(projectTotals.actual.unassigned),
                            },
                          ]
                        : []),
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
                  {(projectData.episodes ?? []).map((ep, index) => {
                    // 只读态不展示费用：演示数据没有对应的真实费用记录。
                    const epCost = readOnly ? undefined : getEpisodeCost(ep.episode);
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
                            {index + 1}
                          </span>
                        )}
                        <span style={{ color: "var(--color-text)", fontFamily: "var(--font-sans)" }}>
                          {ep.title ||
                            (isAd ? projectData.title : t("common:episode_position_name", { position: index + 1 }))}
                        </span>
                        <span style={{ color: "var(--color-text-4)" }}>
                          {t(itemCountKey(route, { withStatus: true }), {
                            count: ep.item_count ?? "?",
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
                              unassignedLabel={t("actual_unassigned_history_short")}
                              unassignedValue={
                                costEntries(epCost.totals.actual.unassigned).length > 0
                                  ? formatCost(epCost.totals.actual.unassigned)
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
      <ConfirmDialog
        open={confirmingRegenerate && !readOnly}
        tone="danger"
        title={t("regen_overview_confirm_title")}
        description={t("regen_overview_confirm_desc")}
        confirmLabel={t("regen_overview_confirm")}
        loadingLabel={t("regenerating_short")}
        loading={regenerating}
        onConfirm={() => handleRegenerate()}
        onCancel={() => setConfirmingRegenerate(false)}
      />
      {!readOnly && <AgentHandoffHint triggerKey={handoffTrigger} storageScope={projectName} />}
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
  unassignedLabel,
  unassignedValue,
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
  unassignedLabel?: string;
  unassignedValue?: string;
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
      {unassignedLabel != null && unassignedValue != null && (
        <>
          <span className="ml-2" style={{ color: "var(--color-text-4)" }}>
            {unassignedLabel}{" "}
          </span>
          <span style={{ color: "var(--color-text-2)" }}>{unassignedValue}</span>
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
