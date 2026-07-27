/**
 * ad + reference_video 的专用画布：整屏按派生分组组织。
 *
 * 广告剧本骨架是镜头列表（shots），分组（unit）由后端从连续镜头派生，
 * 只索引 shot_id 与参考集；成员镜头内容与分组时长按本地剧本水合
 * （shots 是内容唯一真相）。该路径按 ADR 0033 跳过分镜步骤，画布不提供
 * 分镜图生成/上传、Image Prompt 编辑与逐镜头图生视频入口——这些在
 * 参考直出下不参与生成。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Clock, Layers, RefreshCw, Scissors, Sparkles } from "lucide-react";
import { API } from "@/api";
import { enqueueReferenceVideoUnit } from "@/actions/generation";
import { EpisodeHeader } from "./EpisodeHeader";
import { StatusBadge, deriveUnitStatus } from "./unit-status";
import {
  selectActiveResourceIds,
  useActiveResourceIds,
  useLatestTasksByResource,
  useTasksStore,
} from "@/stores/tasks-store";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { errMsg } from "@/utils/async";
import type { AdReferenceUnit, AdShot, UnitStatus } from "@/types";

export interface AdReferenceVideoCanvasProps {
  projectName: string;
  episode: number;
  episodeTitle?: string;
  onSaveTitle?: (next: string) => Promise<void>;
  canEditTitle?: boolean;
  /** 本集剧本的镜头列表；剧本未生成时为空数组 */
  shots: AdShot[];
  /** 剧本（scripts/episode_N.json）是否已生成 */
  hasScript: boolean;
  scriptFile?: string;
  durationOptions?: number[];
  /**
   * 镜头时长/口播文案的轻量编辑写入口；未提供（演示态/只读）时镜头明细
   * 降级为纯文本展示，与其余画布的只读判定保持一致。scriptFile 由本组件
   * 自行透传，与 TimelineCanvas / GridImageToVideoCanvas 同一约定。
   */
  onUpdatePrompt?: (
    segmentId: string,
    fieldOrPatch: string | Record<string, unknown>,
    value?: unknown,
    scriptFile?: string,
  ) => void | Promise<void>;
}

/** 分组视图模型：派生索引 + 本地剧本水合出的成员镜头、时长与失效标记。 */
interface HydratedUnit {
  unit: AdReferenceUnit;
  /** 与 shot_ids 一一对应；镜头已从剧本删除时为 null */
  members: (AdShot | null)[];
  durationSeconds: number;
  /** 存在悬空 shot_id——索引与当前剧本已不一致，需重新派生 */
  stale: boolean;
}

function shotRangeLabel(shotIds: string[]): string {
  if (shotIds.length === 0) return "";
  if (shotIds.length === 1) return shotIds[0];
  return `${shotIds[0]} – ${shotIds[shotIds.length - 1]}`;
}

export function AdReferenceVideoCanvas({
  projectName,
  episode,
  episodeTitle,
  onSaveTitle,
  canEditTitle,
  shots,
  hasScript,
  scriptFile,
  durationOptions,
  onUpdatePrompt,
}: AdReferenceVideoCanvasProps) {
  const { t } = useTranslation("dashboard");
  const [units, setUnits] = useState<AdReferenceUnit[] | null>(null);
  const [deriving, setDeriving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // 剧本未生成时后端无分组可返回；hasScript 转 true 后本 effect 随依赖重跑补上首次拉取。
    if (!hasScript) return;
    const controller = new AbortController();
    API.listAdReferenceUnits(projectName, episode, { signal: controller.signal })
      .then((resp) => {
        if (!controller.signal.aborted) setUnits(resp.units);
      })
      .catch((err: unknown) => {
        // 加载失败保持 units === null（区分「无数据」与「出错」），仅记错误展示
        if (!controller.signal.aborted) setError(errMsg(err));
      });
    return () => controller.abort();
  }, [projectName, episode, hasScript]);

  const shotById = useMemo(() => new Map(shots.map((s) => [s.shot_id, s])), [shots]);

  // 活跃 + 最新行胜出下沉到 store selector：重试的新行不被同 unit 的旧失败行盖住。
  const busyUnitIds = useActiveResourceIds("reference_video", projectName);
  const tasksByUnit = useLatestTasksByResource(projectName, "reference_video");

  const hydrated = useMemo<HydratedUnit[]>(() => {
    return (units ?? []).map((unit) => {
      const members = unit.shot_ids.map((sid) => shotById.get(sid) ?? null);
      return {
        unit,
        members,
        durationSeconds: members.reduce(
          (sum, s) => sum + (typeof s?.duration_seconds === "number" ? s.duration_seconds : 0),
          0,
        ),
        stale: members.some((s) => s === null),
      };
    });
  }, [units, shotById]);

  const statusMap = useMemo<Record<string, UnitStatus>>(() => {
    const map: Record<string, UnitStatus> = {};
    for (const { unit } of hydrated) {
      map[unit.unit_id] = deriveUnitStatus({
        hasClip: Boolean(unit.generated_assets?.video_clip),
        queueRow: tasksByUnit.get(unit.unit_id),
        busy: busyUnitIds.has(unit.unit_id),
        // 本画布不提供成片上传入口（ADR 0033 的参考直出路径）：成片只能来自生成，
        // 最新行失败即本次尝试失败，不能被旧成片压成 ready。
        supportsManualUpload: false,
      });
    }
    return map;
  }, [hydrated, tasksByUnit, busyUnitIds]);

  const headerUnits = useMemo(
    () =>
      hydrated.map((h) => ({
        duration_seconds: h.durationSeconds,
        generated_assets: { video_clip: h.unit.generated_assets?.video_clip ?? null },
      })),
    [hydrated],
  );

  const derive = useCallback(async (): Promise<AdReferenceUnit[]> => {
    // 提交前用 getState() 新鲜读复核：按钮渲染期捕获的 anyUnitBusy 未必反映最新占用态，
    // 命中即中止——避免把仍在跑的旧任务对应的成员重新绑定到派生后的新分组。
    const { tasks, optimisticActive } = useTasksStore.getState();
    const live = selectActiveResourceIds(tasks, "reference_video", projectName, optimisticActive);
    if (hydrated.some(({ unit }) => live.has(unit.unit_id))) {
      useAppStore.getState().pushToast(t("ad_ref_rederive_busy"), "error");
      return [];
    }
    setDeriving(true);
    setError(null);
    try {
      const resp = await API.deriveAdReferenceUnits(projectName, episode);
      setUnits(resp.units);
      return resp.units;
    } catch (err: unknown) {
      setError(errMsg(err));
      return [];
    } finally {
      setDeriving(false);
    }
  }, [projectName, episode, hydrated, t]);

  // 错误清空只在触发入口做：generateUnit 自身不清，避免批量循环中
  // 后一个 unit 的调用抹掉前一个 unit 的失败信息
  const generateUnit = useCallback(
    async (unitId: string) => {
      // 提交前用 getState() 新鲜读复核：卡片渲染期捕获的 busy 未必反映最新占用态
      // （全部生成循环、Agent 入队、SSE 落库均可能在渲染之后、点击之前占用同一 unit）。
      const { tasks, optimisticActive } = useTasksStore.getState();
      if (selectActiveResourceIds(tasks, "reference_video", projectName, optimisticActive).has(unitId)) {
        useAppStore.getState().pushToast(t("ad_ref_busy"), "error");
        return;
      }
      try {
        await enqueueReferenceVideoUnit(projectName, episode, unitId);
      } catch (err: unknown) {
        setError(errMsg(err));
      }
    },
    [projectName, episode, t],
  );

  // 提交前复核最新占用态：展开编辑期间若该 unit 已被其他入口（批量生成、Agent
  // 入队等）占用，放弃这次写入而非与生成中的任务乱序落库——与 generateUnit 同一判定口径。
  const commitShotField = useCallback(
    (unitId: string, shotId: string, patch: Record<string, unknown>) => {
      if (!onUpdatePrompt) return;
      const { tasks, optimisticActive } = useTasksStore.getState();
      if (selectActiveResourceIds(tasks, "reference_video", projectName, optimisticActive).has(unitId)) {
        useAppStore.getState().pushToast(t("ad_ref_busy"), "error");
        return;
      }
      void onUpdatePrompt(shotId, patch, undefined, scriptFile);
    },
    [onUpdatePrompt, projectName, scriptFile, t],
  );

  const generateAll = useCallback(async () => {
    // 先重新派生（保证索引与 shots 一致），再为未完成且空闲的 unit 入队
    const fresh = await derive();
    for (const unit of fresh) {
      // 实时读 store 而非渲染期快照：串行 await 期间其他入口（如单 unit 按钮）
      // 可能已入队同一 unit；乐观标记集也要带上，动作层刚打的标记才能被循环看到
      const { tasks, optimisticActive } = useTasksStore.getState();
      const live = selectActiveResourceIds(tasks, "reference_video", projectName, optimisticActive);
      if (unit.generated_assets?.video_clip || live.has(unit.unit_id)) continue;
      await generateUnit(unit.unit_id);
    }
  }, [derive, generateUnit, projectName]);

  const hasUnits = hydrated.length > 0;
  // 任一分组仍有活跃任务（含取消中）时禁止重新派生：派生会按位置重算 unit_id 的
  // 成员镜头，若此时有任务仍在跑，任务完成落回 apply_unit_video_assets 时会按
  // unit_id 把产物写给重新派生后的新成员，造成成片挂错分组。
  const anyUnitBusy = hydrated.some(({ unit }) => busyUnitIds.has(unit.unit_id));
  // 首次列表 GET 未完成时 units 为 null：此时点击派生，POST 结果可能被随后落地的
  // 首次 GET（携带派生前的旧列表）覆盖，画布会误报尚未派生。禁用入口直到首次加载完成；
  // 加载失败（error 非空）不算「加载中」，否则派生入口会永久禁用、用户无法自救。
  const initialLoadPending = units === null && error === null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <EpisodeHeader
        episode={episode}
        title={episodeTitle ?? `E${episode}`}
        units={headerUnits}
        onSaveTitle={onSaveTitle}
        canEditTitle={canEditTitle}
      />

      <div className="flex flex-wrap items-center gap-2 border-b border-[var(--color-hairline)] bg-[oklch(0.19_0.012_250_/_0.5)] px-5 py-2">
        <Layers className="h-3.5 w-3.5 text-[var(--color-text-3)]" aria-hidden="true" />
        <span className="text-[12.5px] font-medium text-[var(--color-text-2)]">
          {t("ad_ref_units_title")}
        </span>
        <span className="flex-1" />
        <button
          type="button"
          className="sv-navbtn inline-flex items-center gap-1.5"
          disabled={!hasScript || deriving || initialLoadPending || anyUnitBusy}
          onClick={() => void derive()}
        >
          <RefreshCw className="h-3 w-3" aria-hidden="true" />
          <span>{hasUnits ? t("ad_ref_rederive") : t("ad_ref_derive")}</span>
        </button>
        {hasUnits && (
          <button
            type="button"
            className="sv-navbtn inline-flex items-center gap-1.5"
            disabled={deriving || anyUnitBusy}
            onClick={() => void generateAll()}
          >
            <Sparkles className="h-3 w-3" aria-hidden="true" />
            <span>{t("ad_ref_generate_all")}</span>
          </button>
        )}
      </div>

      {error && (
        <p
          role="alert"
          className="border-b border-[var(--color-hairline-soft)] bg-red-500/10 px-5 py-2 text-xs text-red-400"
        >
          {error}
        </p>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto bg-[oklch(0.18_0.011_250_/_0.25)] px-5 py-4">
        {!hasScript ? (
          <p className="py-8 text-center text-[13px] text-[var(--color-text-4)]">
            {t("timeline_script_not_ready")}
          </p>
        ) : !hasUnits ? (
          // 出错时不渲染空态提示，避免「没有数据」与「加载失败」同屏混淆
          !error && (
            <p className="mx-auto max-w-xl py-8 text-center text-[13px] leading-relaxed text-[var(--color-text-4)]">
              {t("ad_ref_empty_hint")}
            </p>
          )
        ) : (
          <ul className="mx-auto flex max-w-5xl flex-col gap-3">
            {hydrated.map(({ unit, members, durationSeconds, stale }) => (
              <AdUnitCard
                key={unit.unit_id}
                unit={unit}
                members={members}
                durationSeconds={durationSeconds}
                stale={stale}
                status={statusMap[unit.unit_id]}
                busy={busyUnitIds.has(unit.unit_id)}
                cancelling={tasksByUnit.get(unit.unit_id)?.status === "cancelling"}
                errorMessage={tasksByUnit.get(unit.unit_id)?.error_message ?? null}
                projectName={projectName}
                deriving={deriving}
                durationOptions={durationOptions ?? []}
                editable={!!onUpdatePrompt}
                onGenerate={(unitId) => {
                  setError(null);
                  void generateUnit(unitId);
                }}
                onEditDuration={(shotId, seconds) =>
                  commitShotField(unit.unit_id, shotId, { duration_seconds: seconds })
                }
                onEditVoiceover={(shotId, textValue) =>
                  commitShotField(unit.unit_id, shotId, { voiceover_text: textValue })
                }
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

interface AdUnitCardProps {
  unit: AdReferenceUnit;
  members: (AdShot | null)[];
  durationSeconds: number;
  stale: boolean;
  status: UnitStatus;
  /**
   * 占用集（含入队后真实任务行落库前的乐观标记）命中与否，独立于 status：
   * status 的乐观分支只在无任务行时生效（保持 cancelling 不显示为生成中），
   * 重试与重新生成这两条路径上旧任务行始终在，仅看 status 会在乐观窗口内漏禁用。
   */
  busy: boolean;
  /** 最新任务行是否处于取消中——占用集会计入 cancelling，但不应展示为「生成中」。 */
  cancelling: boolean;
  errorMessage: string | null;
  projectName: string;
  deriving: boolean;
  durationOptions: number[];
  /** 未传 onUpdatePrompt（演示态/只读）时降级为纯文本展示，不渲染编辑控件。 */
  editable: boolean;
  onGenerate: (unitId: string) => void;
  onEditDuration: (shotId: string, seconds: number) => void;
  onEditVoiceover: (shotId: string, text: string) => void;
}

function AdUnitCard({
  unit,
  members,
  durationSeconds,
  stale,
  status,
  busy,
  cancelling,
  errorMessage,
  projectName,
  deriving,
  durationOptions,
  editable,
  onGenerate,
  onEditDuration,
  onEditVoiceover,
}: AdUnitCardProps) {
  const { t } = useTranslation("dashboard");
  const clip = unit.generated_assets?.video_clip ?? null;
  // 生成/还原后路径不变，靠 fingerprint cache-bust 让 <video> 重新拉取
  const clipFp = useProjectsStore((s) => (clip ? s.getAssetFingerprint(clip) : null));
  const videoUrl = clip ? API.getFileUrl(projectName, clip, clipFp) : null;

  // 状态先于 video_clip 落库的窗口里 status 已 ready 但 videoUrl 仍为 null——
  // 这种情况按生成中占位，避免空白预览框。busy 一并计入，使重试/重新生成在
  // 乐观窗口内也占位；但 cancelling 时排除在外——取消中不是「生成中」，展示层
  // 沿用取消前的状态，仅按钮仍需保持禁用（见下方 disabled）。生成中优先于
  // ready/failed，三者互斥。
  const inFlight = (busy && !cancelling) || status === "running" || (status === "ready" && !videoUrl);
  const ready = status === "ready" && Boolean(videoUrl) && !inFlight;
  const failed = status === "failed" && !inFlight;

  const ctaLabel = inFlight
    ? t("ad_ref_generating")
    : failed
      ? t("ad_ref_retry")
      : ready
        ? t("ad_ref_regenerate")
        : t("ad_ref_generate_unit");

  return (
    <li className="overflow-hidden rounded-lg border border-[var(--color-hairline)] bg-[oklch(0.19_0.012_250_/_0.5)]">
      <div className="flex flex-wrap items-center gap-2 border-b border-[var(--color-hairline-soft)] px-3.5 py-2.5">
        <span
          translate="no"
          className="rounded px-2.5 py-1 font-mono text-xs font-bold tracking-wider text-[oklch(0.14_0_0)] [background:linear-gradient(180deg,var(--color-accent-2),var(--color-accent))] shadow-[inset_0_1px_0_oklch(1_0_0_/_0.3),0_2px_6px_-2px_var(--color-accent-glow)]"
        >
          {unit.unit_id}
        </span>
        <span className="font-mono text-[11.5px] text-[var(--color-text-3)]" translate="no">
          {shotRangeLabel(unit.shot_ids)}
        </span>
        <span className="inline-flex items-center gap-1 rounded border border-[var(--color-hairline-soft)] bg-[oklch(0.22_0.011_265_/_0.6)] px-2 py-0.5 text-[11.5px] text-[var(--color-text-2)]">
          <Clock className="h-3 w-3" aria-hidden="true" />
          <span className="font-mono tabular-nums">{durationSeconds}s</span>
        </span>
        <span className="inline-flex items-center gap-1 rounded border border-[var(--color-hairline-soft)] bg-[oklch(0.22_0.011_265_/_0.6)] px-2 py-0.5 text-[11.5px] text-[var(--color-text-2)]">
          <Scissors className="h-3 w-3" aria-hidden="true" />
          <span className="font-mono tabular-nums">
            {t("reference_unit_shots_count", { count: unit.shot_ids.length })}
          </span>
        </span>
        <span className="flex-1" />
        {stale && (
          <span className="text-[11px] text-amber-300">{t("ad_ref_stale")}</span>
        )}
        <StatusBadge status={status} size="md" />
      </div>

      <div className="flex flex-wrap gap-4 p-3.5">
        <div className="min-w-[260px] flex-1">
          <h4 className="mb-2 font-mono text-[10px] font-semibold uppercase tracking-wider text-[var(--color-text-4)]">
            {t("ad_ref_member_shots")}
          </h4>
          {editable && clip && (
            <p className="mb-1.5 text-[11px] text-amber-300">{t("ad_ref_edit_regenerate_hint")}</p>
          )}
          <ol className="flex flex-col gap-1.5">
            {members.map((shot, i) => {
              const shotId = unit.shot_ids[i];
              if (editable && shot) {
                return (
                  <li key={shotId}>
                    <ShotLightEditor
                      shot={shot}
                      durationOptions={durationOptions}
                      disabled={busy}
                      onCommitDuration={onEditDuration}
                      onCommitVoiceover={onEditVoiceover}
                    />
                  </li>
                );
              }
              return (
                <li
                  key={shotId}
                  className="rounded bg-[oklch(0.22_0.012_250_/_0.5)] px-2.5 py-1.5 text-[12px]"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span
                      className="font-mono font-medium text-[var(--color-text-2)]"
                      translate="no"
                    >
                      {shotId}
                    </span>
                    {shot?.section && (
                      <span className="text-[11px] text-[var(--color-text-4)]">{shot.section}</span>
                    )}
                    {shot && (
                      <span className="font-mono text-[11px] tabular-nums text-[var(--color-text-4)]">
                        {shot.duration_seconds}s
                      </span>
                    )}
                    {!shot && (
                      <span className="text-[11px] text-amber-300">{t("ad_ref_shot_missing")}</span>
                    )}
                  </div>
                  {shot?.voiceover_text && (
                    <p className="mt-1 line-clamp-2 text-[11.5px] leading-relaxed text-[var(--color-text-3)]">
                      {shot.voiceover_text}
                    </p>
                  )}
                </li>
              );
            })}
          </ol>

          {unit.references.length > 0 && (
            <>
              <h4 className="mb-1.5 mt-3 font-mono text-[10px] font-semibold uppercase tracking-wider text-[var(--color-text-4)]">
                {t("ad_ref_references")}
              </h4>
              <ul className="flex flex-wrap gap-1.5">
                {unit.references.map((ref) => (
                  <li
                    key={`${ref.type}:${ref.name}`}
                    className="rounded border border-[var(--color-hairline-soft)] bg-[oklch(0.22_0.011_265_/_0.6)] px-2 py-0.5 text-[11px] text-[var(--color-text-2)]"
                  >
                    {ref.name}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>

        <div className="flex w-[260px] shrink-0 flex-col gap-2">
          <div
            className={`relative aspect-video w-full overflow-hidden rounded-lg border border-[var(--color-hairline)] ${
              ready
                ? "bg-[linear-gradient(135deg,oklch(0.32_0.04_240),oklch(0.18_0.02_280))]"
                : "bg-[oklch(0.18_0.010_265_/_0.5)]"
            }`}
          >
            {ready && videoUrl && (
              /* eslint-disable-next-line jsx-a11y/media-has-caption -- AI 生成的成片没有字幕轨 */
              <video
                src={videoUrl}
                aria-label={t("ad_ref_preview_video_aria", { id: unit.unit_id })}
                controls
                preload="metadata"
                playsInline
                className="h-full w-full object-contain"
              />
            )}
            {inFlight && (
              <div className="absolute inset-0 grid place-items-center">
                <div className="h-8 w-8 animate-spin rounded-full border-2 border-[var(--color-accent-soft)] border-t-[var(--color-accent)]" />
              </div>
            )}
            {failed && (
              <p className="absolute inset-0 grid place-items-center px-4 text-center text-[11px] leading-relaxed text-red-300">
                {errorMessage ?? t("ad_ref_failed_unknown")}
              </p>
            )}
            {!ready && !inFlight && !failed && (
              <p className="absolute inset-0 grid place-items-center text-[11.5px] text-[var(--color-text-4)]">
                {t("ad_ref_no_video")}
              </p>
            )}
          </div>

          <button
            type="button"
            className="sv-navbtn inline-flex items-center justify-center gap-1.5"
            disabled={inFlight || busy || stale || deriving}
            onClick={() => onGenerate(unit.unit_id)}
          >
            <Sparkles className="h-3 w-3" aria-hidden="true" />
            <span>{ctaLabel}</span>
          </button>

          {videoUrl && (
            <a
              href={videoUrl}
              target="_blank"
              rel="noreferrer"
              className="focus-ring text-center text-[11.5px] text-[var(--color-accent)] underline"
            >
              {t("ad_ref_view_video")}
            </a>
          )}
        </div>
      </div>
    </li>
  );
}

interface ShotLightEditorProps {
  shot: AdShot;
  durationOptions: number[];
  disabled: boolean;
  onCommitDuration: (shotId: string, seconds: number) => void;
  onCommitVoiceover: (shotId: string, text: string) => void;
}

/**
 * 镜头级轻量编辑：时长本质是生视频 prompt 的文本控制而非真实视频时长，
 * 故用即选即提交的下拉框而非重交互控件；口播文案失焦提交，避免逐键触发写请求。
 */
function ShotLightEditor({
  shot,
  durationOptions,
  disabled,
  onCommitDuration,
  onCommitVoiceover,
}: ShotLightEditorProps) {
  const { t } = useTranslation("dashboard");
  const [draftText, setDraftText] = useState<string | null>(null);
  const text = draftText ?? shot.voiceover_text;
  // 时长能力尚未解析出来时，至少保留镜头当前值可选，不虚构范围
  const options = durationOptions.length > 0 ? durationOptions : [shot.duration_seconds];

  return (
    <div className="flex flex-wrap items-center gap-2 rounded bg-[oklch(0.22_0.012_250_/_0.5)] px-2.5 py-1.5 text-[12px]">
      <span className="font-mono font-medium text-[var(--color-text-2)]" translate="no">
        {shot.shot_id}
      </span>
      {shot.section && <span className="text-[11px] text-[var(--color-text-4)]">{shot.section}</span>}
      <select
        aria-label={t("ad_ref_shot_duration_label", { shot_id: shot.shot_id })}
        className="focus-ring rounded border border-[var(--color-hairline-soft)] bg-[oklch(0.22_0.011_265_/_0.6)] px-1.5 py-0.5 text-[11.5px] text-[var(--color-text-2)]"
        value={shot.duration_seconds}
        disabled={disabled}
        onChange={(e) => onCommitDuration(shot.shot_id, parseInt(e.target.value, 10))}
      >
        {options.map((d) => (
          <option key={d} value={d}>
            {t("duration_seconds_value_text", { value: d })}
          </option>
        ))}
      </select>
      <input
        type="text"
        aria-label={t("ad_ref_shot_voiceover_label", { shot_id: shot.shot_id })}
        className="focus-ring min-w-0 flex-1 rounded border border-[var(--color-hairline-soft)] bg-[oklch(0.22_0.011_265_/_0.6)] px-1.5 py-0.5 text-[11.5px] text-[var(--color-text-2)]"
        value={text}
        disabled={disabled}
        onChange={(e) => setDraftText(e.target.value)}
        onBlur={() => {
          if (draftText !== null && draftText !== shot.voiceover_text) {
            onCommitVoiceover(shot.shot_id, draftText);
          }
          setDraftText(null);
        }}
        placeholder={t("detail_voiceover_placeholder")}
      />
    </div>
  );
}
