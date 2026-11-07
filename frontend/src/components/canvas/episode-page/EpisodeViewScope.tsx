import { createContext, useCallback, useContext } from "react";
import { useLocation, useRouter, useSearch } from "wouter";

import { staysInEpisodeView, type EpisodeViewFacts } from "./episode-view";

/** Episode-view facts supplied by `EpisodePage` to canvas editing units; null outside episode pages. */
const EpisodeViewFactsContext = createContext<EpisodeViewFacts | null>(null);

export const EpisodeViewFactsProvider = EpisodeViewFactsContext.Provider;

const NEVER = () => false;

/**
 * Canvas editing-unit `allowNavigation`: allow only in-app navigation that remains in the current view (see `staysInEpisodeView`).
 * wouter includes the nested-route base in navigation-guard destinations; construct the current address in the same form before comparing.
 * Always block outside episode pages.
 */
export function useStaysInEpisodeView(): (to: string) => boolean {
  const facts = useContext(EpisodeViewFactsContext);
  const { base } = useRouter();
  const [path] = useLocation();
  const search = useSearch();
  const here = `${base}${path}${search ? `?${search}` : ""}`;
  const allow = useCallback((to: string) => (facts ? staysInEpisodeView(to, here, facts) : false), [facts, here]);
  return facts ? allow : NEVER;
}
