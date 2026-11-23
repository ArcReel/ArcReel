import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";

import { useProjectsStore } from "@/stores/projects-store";
import { itemPositionLabel } from "@/utils/episode-display";
import { episodeItemKind, findEpisodeItem, type EpisodeItemFacts } from "@/utils/episode-item";

export function useEpisodeItem(itemId: string): EpisodeItemFacts | null {
  const scripts = useProjectsStore((s) => s.currentScripts);
  return useMemo(() => findEpisodeItem(scripts, itemId), [scripts, itemId]);
}

export function useItemPositionLabel(itemId: string): string {
  const { t } = useTranslation();
  const item = useEpisodeItem(itemId);
  return itemPositionLabel(episodeItemKind(itemId), item?.position ?? null, t);
}

export function useItemPositionLabeler(): (itemId: string) => string {
  const { t } = useTranslation();
  const scripts = useProjectsStore((s) => s.currentScripts);
  return useCallback(
    (itemId: string) => itemPositionLabel(episodeItemKind(itemId), findEpisodeItem(scripts, itemId)?.position ?? null, t),
    [scripts, t],
  );
}
