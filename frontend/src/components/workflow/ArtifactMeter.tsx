import { useTranslation } from "react-i18next";
import type { ArtifactStatus, WorkflowArtifactCollection } from "@/types/workflow";
import { ARTIFACT_STATUSES, ARTIFACT_TONES, artifactFill } from "./state-language";

/** 集合按时效分出的四组 id。集合读不了时这里为 null，一个 id 都不猜。 */
export interface ArtifactCounts {
  current: string[];
  stale: string[];
  missing: string[];
  blocked: string[];
}

function idList(collection: WorkflowArtifactCollection, key: string): string[] {
  const value = collection[key];
  return Array.isArray(value) ? value.filter((id): id is string => typeof id === "string") : [];
}

/**
 * 把一个产物集合读成四组 id。
 *
 * 返回 `null` 表示这不是一个可枚举的集合——要么容器本身读不了（`state: "blocked"`），
 * 要么这一步的产物是单份而非集合。两种情况都不能拿计量条陈述，否则「读不出来」会被
 * 呈现成「零件都缺」。
 */
export function artifactCounts(collection: WorkflowArtifactCollection): ArtifactCounts | null {
  const current = idList(collection, "current_ids");
  const stale = idList(collection, "stale_ids");
  const missing = idList(collection, "missing_ids");
  const hasLists =
    "current_ids" in collection || "stale_ids" in collection || "missing_ids" in collection;
  if (!hasLists || collection.state === "blocked") return null;
  return { current, stale, missing, blocked: [] };
}

/** 集合级计量条：形状区分时效，灰度下依然可读，颜色只区分程度。 */
function Meter({ counts }: { counts: ArtifactCounts }) {
  const total =
    counts.current.length + counts.stale.length + counts.missing.length + counts.blocked.length;
  if (total === 0) return null;
  return (
    <span aria-hidden className="flex h-2 w-full min-w-24 gap-px overflow-hidden rounded-full">
      {ARTIFACT_STATUSES.map((status) => {
        const size = counts[status].length;
        if (size === 0) return null;
        return (
          <span
            key={status}
            className="h-full rounded-full"
            style={{ flexGrow: size, flexBasis: 0, ...artifactFill(status) }}
          />
        );
      })}
    </span>
  );
}

interface Props {
  collection: WorkflowArtifactCollection;
  /** 该步骤已有的进行中任务数；用于说明「读不出来」与「正在跑」不是一回事。 */
  className?: string;
}

/**
 * 一个步骤的产物时效摘要。
 *
 * 计数是权威陈述，计量条只是它的图形复述（`aria-hidden`）——辅助技术读到的永远是
 * 「可继续使用 5 件，其中 2 件已过时；还缺 1 件」这样的完整句子，不是一条无名的色带。
 *
 * stale 与 missing 分列且措辞不同：前者是「还能用，只是比当前内容旧」，后者是「还没有」。
 * 把它们合成一个「未完成」数字，就等于建议用户把已经付费的产物重做一遍。
 */
export function ArtifactMeter({ collection, className }: Props) {
  const { t } = useTranslation("workflow");
  const state = collection.state;

  if (state === "not_applicable") {
    return (
      <p className={className} style={{ color: "var(--color-text-4)" }}>
        {t("artifact_not_applicable")}
      </p>
    );
  }

  const counts = artifactCounts(collection);

  if (counts === null) {
    // 单份产物，或集合容器读不了。两者都只有一个状态词可讲，不摊计量条。
    const single: ArtifactStatus = state === "blocked" ? "blocked" : (state as ArtifactStatus);
    if (!single) return null;
    return (
      <p className={className} style={{ color: ARTIFACT_TONES[single].color }}>
        {state === "blocked" ? t("artifact_collection_blocked") : t(`artifact_${single}`)}
      </p>
    );
  }

  const usable = counts.current.length + counts.stale.length;
  const total = usable + counts.missing.length;
  if (total === 0) {
    return (
      <p className={className} style={{ color: "var(--color-text-4)" }}>
        {t("artifact_empty")}
      </p>
    );
  }

  return (
    <div className={className ?? "space-y-1"}>
      <Meter counts={counts} />
      <p className="text-[11.5px] tabular-nums" style={{ color: "var(--color-text-3)" }}>
        {t("artifact_usable", { count: usable })}
        {counts.stale.length > 0 && (
          <>
            {" · "}
            <span style={{ color: ARTIFACT_TONES.stale.color }}>
              {t("artifact_stale_of_usable", { count: counts.stale.length })}
            </span>
          </>
        )}
        {counts.missing.length > 0 && (
          <>
            {" · "}
            {t("artifact_missing_count", { count: counts.missing.length })}
          </>
        )}
      </p>
    </div>
  );
}
