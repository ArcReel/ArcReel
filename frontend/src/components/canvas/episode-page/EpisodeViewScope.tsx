import { createContext, useCallback, useContext } from "react";
import { useLocation, useRouter, useSearch } from "wouter";

import { staysInEpisodeView, type EpisodeViewFacts } from "./episode-view";

const EpisodeViewFactsContext = createContext<EpisodeViewFacts | null>(null);

export const EpisodeViewFactsProvider = EpisodeViewFactsContext.Provider;

const NEVER = () => false;

export function useStaysInEpisodeView(): (to: string) => boolean {
  const facts = useContext(EpisodeViewFactsContext);
  const { base } = useRouter();
  const [path] = useLocation();
  const search = useSearch();
  const here = `${base}${path}${search ? `?${search}` : ""}`;
  const allow = useCallback((to: string) => (facts ? staysInEpisodeView(to, here, facts) : false), [facts, here]);
  return facts ? allow : NEVER;
}
