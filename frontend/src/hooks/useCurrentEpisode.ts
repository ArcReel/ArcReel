import { useRoute } from "wouter";
import { WORKSPACE_ROUTE_EPISODES } from "@/app-routes";

/** 集级路由 path，与渲染该集的 `<Route>` 共用一份，避免两处字面量各自漂移。 */
export const EPISODE_ROUTE_PATH = `/${WORKSPACE_ROUTE_EPISODES}/:episodeId`;

/**
 * 当前路由所在的集号；不在集级路由下（或集号段不是数字）时为 undefined。
 *
 * 能力查询的集号出口：生成模式可被单集覆盖，服务端要拿到集号才会按该集生效模式解析
 * `voiceConsistency` / `lastFrame` 等二维派生值。各消费方共用本 hook，不各自解析路由——
 * 否则同一页的几个能力出口会因集号口径不同而拿到互相矛盾的能力。
 */
export function useCurrentEpisode(): number | undefined {
  const [, params] = useRoute<{ episodeId: string }>(EPISODE_ROUTE_PATH);
  const parsed = params ? parseInt(params.episodeId, 10) : NaN;
  return Number.isFinite(parsed) ? parsed : undefined;
}
