import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";

import { useProjectsStore } from "@/stores/projects-store";
import { itemPositionLabel } from "@/utils/episode-display";
import { episodeItemKind, findEpisodeItem, type EpisodeItemFacts } from "@/utils/episode-item";

/** 当前项目剧本里的条目事实（位置、总数、摘录、缩略图）；条目已不在剧本里时为 null。 */
export function useEpisodeItem(itemId: string): EpisodeItemFacts | null {
  const scripts = useProjectsStore((s) => s.currentScripts);
  return useMemo(() => findEpisodeItem(scripts, itemId), [scripts, itemId]);
}

/** 称呼条目的文字：「分镜 5」「视频单元 5」，条目已不在剧本里时「已删除」。用于确认框标题、可访问名称这类句子。 */
export function useItemPositionLabel(itemId: string): string {
  const { t } = useTranslation();
  const item = useEpisodeItem(itemId);
  return itemPositionLabel(episodeItemKind(itemId), item?.position ?? null, t);
}

/** 列表里逐条称呼条目的函数：`(itemId) => 「分镜 5」`，位置按当前项目剧本现算。 */
export function useItemPositionLabeler(): (itemId: string) => string {
  const { t } = useTranslation();
  const scripts = useProjectsStore((s) => s.currentScripts);
  return useCallback(
    (itemId: string) => itemPositionLabel(episodeItemKind(itemId), findEpisodeItem(scripts, itemId)?.position ?? null, t),
    [scripts, t],
  );
}
