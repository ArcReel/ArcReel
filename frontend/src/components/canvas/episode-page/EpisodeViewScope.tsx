import { createContext, useCallback, useContext } from "react";
import { useLocation, useRouter, useSearch } from "wouter";

import { staysInEpisodeView, type EpisodeViewFacts } from "./episode-view";

/** 集页的视图事实，由 `EpisodePage` 提供给画布里的编辑单元。不在集页里时为 null。 */
const EpisodeViewFactsContext = createContext<EpisodeViewFacts | null>(null);

export const EpisodeViewFactsProvider = EpisodeViewFactsContext.Provider;

const NEVER = () => false;

/**
 * 画布里编辑单元的 `allowNavigation`：只放行跳转后仍停留在当前视图的应用内跳转（见 `staysInEpisodeView`）。
 * wouter 交给离开拦截的目标地址带嵌套路由的 base，这里拼出同样形式的当前地址再比较。
 * 不在集页里时一律拦截。
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
