import { useProjectsStore } from "@/stores/projects-store";
import type { EpisodeLedger } from "@/utils/episode-display";

const EMPTY_LEDGER: EpisodeLedger = [];

/** Current project episode ledger in broadcast order; empty before the project loads. */
export function useEpisodeLedger(): EpisodeLedger {
  return useProjectsStore((s) => s.currentProjectData?.episodes) ?? EMPTY_LEDGER;
}
