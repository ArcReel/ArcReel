import { useProjectsStore } from "@/stores/projects-store";
import type { EpisodeLedger } from "@/utils/episode-display";

const EMPTY_LEDGER: EpisodeLedger = [];

export function useEpisodeLedger(): EpisodeLedger {
  return useProjectsStore((s) => s.currentProjectData?.episodes) ?? EMPTY_LEDGER;
}
