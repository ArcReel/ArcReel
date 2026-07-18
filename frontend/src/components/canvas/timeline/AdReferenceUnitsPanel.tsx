/**
 * ad + reference_video 的派生分组面板。
 *
 * 展示从 shots 派生的 video_unit 轻量索引（unit → shot_ids + 参考集），
 * 成员镜头范围与总时长按本地剧本（shots，内容唯一真相）水合；
 * 提供（重新）派生分组与逐 unit / 全部生成入口。
 */

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Layers, RefreshCw, Sparkles } from "lucide-react";
import { API } from "@/api";
import { enqueueReferenceVideoUnit } from "@/actions/generation";
import {
  selectActiveResourceIds,
  useActiveResourceIds,
  useTasksStore,
} from "@/stores/tasks-store";
import type { AdReferenceUnit, AdShot } from "@/types";

interface AdReferenceUnitsPanelProps {
  projectName: string;
  episode: number;
  shots: AdShot[];
}

function shotRangeLabel(shotIds: string[]): string {
  if (shotIds.length === 0) return "";
  if (shotIds.length === 1) return shotIds[0];
  return `${shotIds[0]} – ${shotIds[shotIds.length - 1]}`;
}

export function AdReferenceUnitsPanel({ projectName, episode, shots }: AdReferenceUnitsPanelProps) {
  const { t } = useTranslation("dashboard");
  const [units, setUnits] = useState<AdReferenceUnit[] | null>(null);
  const [deriving, setDeriving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    API.listAdReferenceUnits(projectName, episode)
      .then((resp) => {
        if (!cancelled) setUnits(resp.units);
      })
      .catch((err: unknown) => {
        // 加载失败保持 units === null（区分「无数据」与「出错」），仅记错误展示
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectName, episode]);

  const shotById = useMemo(() => new Map(shots.map((s) => [s.shot_id, s])), [shots]);

  // 活跃 + 最新行胜出下沉到 store selector：重试的新行不被同 unit 的旧失败行盖住。
  const busyUnitIds = useActiveResourceIds("reference_video", projectName);

  const derive = async (): Promise<AdReferenceUnit[]> => {
    setDeriving(true);
    setError(null);
    try {
      const resp = await API.deriveAdReferenceUnits(projectName, episode);
      setUnits(resp.units);
      return resp.units;
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
      return [];
    } finally {
      setDeriving(false);
    }
  };

  // 错误清空只在触发入口做：generateUnit 自身不清，避免批量循环中
  // 后一个 unit 的调用抹掉前一个 unit 的失败信息
  const generateUnit = async (unitId: string) => {
    try {
      await enqueueReferenceVideoUnit(projectName, episode, unitId);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  // 实时读 store 而非渲染期快照：串行 await 期间其他入口（如单 unit 按钮）
  // 可能已入队同一 unit；乐观标记集也要带上，动作层刚打的标记才能被循环看到
  const liveBusyUnitIds = () => {
    const { tasks, optimisticActive } = useTasksStore.getState();
    return selectActiveResourceIds(tasks, "reference_video", projectName, optimisticActive);
  };

  const generateAll = async () => {
    // 先重新派生（保证索引与 shots 一致），再为未完成且空闲的 unit 入队
    const fresh = await derive();
    for (const unit of fresh) {
      if (unit.generated_assets?.video_clip || liveBusyUnitIds().has(unit.unit_id)) continue;
      await generateUnit(unit.unit_id);
    }
  };

  if (units === null && error === null) return null;

  const unitList = units ?? [];
  const hasUnits = unitList.length > 0;

  return (
    <div
      className="mx-4 mt-3 rounded-lg border px-3.5 py-3"
      style={{ borderColor: "var(--color-hairline)", background: "oklch(0.19 0.012 250 / 0.5)" }}
    >
      <div className="flex items-center gap-2">
        <Layers className="h-3.5 w-3.5" style={{ color: "var(--color-text-3)" }} aria-hidden="true" />
        <span className="text-[12.5px] font-medium" style={{ color: "var(--color-text-2)" }}>
          {t("ad_ref_units_title")}
        </span>
        <span className="flex-1" />
        <button
          type="button"
          className="sv-navbtn inline-flex items-center gap-1.5"
          disabled={deriving}
          onClick={() => void derive()}
        >
          <RefreshCw className="h-3 w-3" aria-hidden="true" />
          <span>{hasUnits ? t("ad_ref_rederive") : t("ad_ref_derive")}</span>
        </button>
        {hasUnits && (
          <button
            type="button"
            className="sv-navbtn inline-flex items-center gap-1.5"
            disabled={deriving}
            onClick={() => void generateAll()}
          >
            <Sparkles className="h-3 w-3" aria-hidden="true" />
            <span>{t("ad_ref_generate_all")}</span>
          </button>
        )}
      </div>

      {/* 出错时不渲染空态提示，避免「没有数据」与「加载失败」同屏混淆 */}
      {!hasUnits && !error && (
        <p className="mt-2 text-[12px]" style={{ color: "var(--color-text-4)" }}>
          {t("ad_ref_empty_hint")}
        </p>
      )}

      {hasUnits && (
        <ul className="mt-2 space-y-1.5">
          {unitList.map((unit) => {
            const memberShots = unit.shot_ids.map((sid) => shotById.get(sid));
            const stale = memberShots.some((s) => s === undefined);
            const duration = memberShots.reduce(
              (sum, s) => sum + (typeof s?.duration_seconds === "number" ? s.duration_seconds : 0),
              0,
            );
            const clip = unit.generated_assets?.video_clip ?? null;
            const videoUrl = clip ? API.getFileUrl(projectName, clip) : null;
            const busy = busyUnitIds.has(unit.unit_id);
            return (
              <li
                key={unit.unit_id}
                className="flex items-center gap-3 rounded px-2 py-1.5 text-[12px]"
                style={{ background: "oklch(0.22 0.012 250 / 0.5)" }}
              >
                <span className="font-mono font-medium" style={{ color: "var(--color-text-2)" }}>
                  {unit.unit_id}
                </span>
                <span style={{ color: "var(--color-text-3)" }}>{shotRangeLabel(unit.shot_ids)}</span>
                <span style={{ color: "var(--color-text-4)" }}>{duration}s</span>
                {unit.references.length > 0 && (
                  <span className="truncate" style={{ color: "var(--color-text-4)" }}>
                    {unit.references.map((r) => r.name).join(" / ")}
                  </span>
                )}
                <span className="flex-1" />
                {stale && (
                  <span style={{ color: "var(--color-warning, oklch(0.75 0.15 80))" }}>
                    {t("ad_ref_stale")}
                  </span>
                )}
                {videoUrl && (
                  <a
                    href={videoUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="underline"
                    style={{ color: "var(--color-accent)" }}
                  >
                    {t("ad_ref_view_video")}
                  </a>
                )}
                <button
                  type="button"
                  className="sv-navbtn inline-flex items-center gap-1"
                  disabled={busy || stale || deriving}
                  onClick={() => {
                    setError(null);
                    void generateUnit(unit.unit_id);
                  }}
                >
                  <Sparkles className="h-3 w-3" aria-hidden="true" />
                  <span>{busy ? t("ad_ref_generating") : t("ad_ref_generate_unit")}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {error && (
        <p className="mt-2 text-[12px]" role="alert" style={{ color: "var(--color-danger, oklch(0.65 0.2 25))" }}>
          {error}
        </p>
      )}
    </div>
  );
}
