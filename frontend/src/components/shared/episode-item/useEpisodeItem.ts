import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";

import { useProjectsStore } from "@/stores/projects-store";
import { itemPositionLabel } from "@/utils/episode-display";
import { episodeItemKind, findEpisodeItem, type EpisodeItemFacts } from "@/utils/episode-item";

/** Entry facts in the current project script (position, total, excerpt, thumbnail); null if the entry no longer exists in the script. */
export function useEpisodeItem(itemId: string): EpisodeItemFacts | null {
  const scripts = useProjectsStore((s) => s.currentScripts);
  return useMemo(() => findEpisodeItem(scripts, itemId), [scripts, itemId]);
}

/** Entry label: Storyboard 5 or Video unit 5, or Deleted when absent from the script. Used in confirmation titles, accessible names and similar sentences. */
export function useItemPositionLabel(itemId: string): string {
  const { t } = useTranslation();
  const item = useEpisodeItem(itemId);
  return itemPositionLabel(episodeItemKind(itemId), item?.position ?? null, t);
}

/** Function naming each entry in a list: `(itemId) => 「分镜 5」`, recomputing position from the current project script. */
export function useItemPositionLabeler(): (itemId: string) => string {
  const { t } = useTranslation();
  const scripts = useProjectsStore((s) => s.currentScripts);
  return useCallback(
    (itemId: string) => itemPositionLabel(episodeItemKind(itemId), findEpisodeItem(scripts, itemId)?.position ?? null, t),
    [scripts, t],
  );
}
