/**
 * 集页视图路由：当前视图记在地址的 `?view=` 上，取值与可用条件只在这里定义。
 *
 * | 取值 | 视图 | 何时出现 |
 * | --- | --- | --- |
 * | `plan` | 脚本规划（内容确认页；剧本未生成时是集原文确认） | 非广告项目，有脚本规划中间稿、集原文待确认，或参考生视频项目 |
 * | `grid` | 多宫格分镜图 | 分镜图生视频且开启宫格；集原文待确认时不可选 |
 * | `board` | 分镜（参考生视频项目称「视频单元」） | 恒有；有脚本规划却还没有正式剧本时不可选 |
 * | `edit` | 剪辑 | 有正式剧本的非演示项目 |
 *
 * 缺省视图：有正式剧本时是 `board`，否则有脚本规划时是 `plan`。地址只在所选视图不是缺省视图时
 * 写 `view`，剧本生成后停在缺省视图上的页面因此自动落到分镜。取值无效或当前不可选时按缺省视图显示。
 */
import { EPISODE_VIEW_EDIT, EPISODE_VIEW_PARAM } from "@/app-routes";
import type { GenerationRoute } from "@/utils/generation-mode";

export const EPISODE_VIEWS = ["plan", "grid", "board", EPISODE_VIEW_EDIT] as const;
export type EpisodeView = (typeof EPISODE_VIEWS)[number];
/** 画布承担的视图；剪辑视图替换整个画布。 */
export type CanvasView = Exclude<EpisodeView, "edit">;

/** 决定集页有哪些视图的事实，由路由层按项目与这一集算出。 */
export interface EpisodeViewFacts {
  isAd: boolean;
  route: GenerationRoute;
  /** 分镜图生视频且开启了宫格分镜图。 */
  grid: boolean;
  /** 已有正式剧本。 */
  hasScript: boolean;
  /** 已有脚本规划中间稿（`script_status` 为 segmented 或 generated）。 */
  hasDraft: boolean;
  /** 剧本与中间稿都没有，脚本规划视图显示集原文确认。 */
  sourceReview: boolean;
  /** 演示项目：没有剪辑视图。 */
  demo: boolean;
}

export interface EpisodeViewTab {
  view: EpisodeView;
  disabled: boolean;
}

function planAvailable(facts: EpisodeViewFacts): boolean {
  if (facts.isAd) return false;
  return facts.sourceReview || facts.hasDraft || (facts.route === "reference_video" && facts.hasScript);
}

/** 集页页头的视图 tab，按显示顺序排列。 */
export function episodeViewTabs(facts: EpisodeViewFacts): EpisodeViewTab[] {
  const plan = planAvailable(facts);
  const tabs: EpisodeViewTab[] = [];
  if (plan) tabs.push({ view: "plan", disabled: false });
  if (facts.grid) tabs.push({ view: "grid", disabled: facts.sourceReview });
  // 参考生视频的单元列表在中间稿阶段就可查看；分镜图生视频要等正式剧本。没有脚本规划时分镜是唯一视图。
  const boardEnabled =
    facts.hasScript || !plan || (facts.route === "reference_video" && !facts.sourceReview);
  tabs.push({ view: "board", disabled: !boardEnabled });
  if (facts.hasScript && !facts.demo) tabs.push({ view: EPISODE_VIEW_EDIT, disabled: false });
  return tabs;
}

export function defaultEpisodeView(facts: EpisodeViewFacts): EpisodeView {
  if (facts.hasScript) return "board";
  return planAvailable(facts) ? "plan" : "board";
}

/** 地址上的取值落到哪个视图：无效或不可选时按缺省视图。 */
export function resolveEpisodeView(requested: string | null, facts: EpisodeViewFacts): EpisodeView {
  const tab = episodeViewTabs(facts).find((entry) => entry.view === requested);
  return tab && !tab.disabled ? tab.view : defaultEpisodeView(facts);
}

/**
 * 切到 `next` 后的查询参数：缺省视图不写 `view`，其余参数原样保留。返回新对象、不改动传入的参数——
 * 跳转被离开拦截挡下时，当前地址的参数不能已经变了。
 */
export function withEpisodeView(params: URLSearchParams, next: EpisodeView, facts: EpisodeViewFacts): URLSearchParams {
  const result = new URLSearchParams(params);
  if (next === defaultEpisodeView(facts)) result.delete(EPISODE_VIEW_PARAM);
  else result.set(EPISODE_VIEW_PARAM, next);
  return result;
}

/**
 * 应用内跳转 `to` 是否只在同一集的画布视图之间切换（脚本规划、多宫格分镜图、分镜），画布不卸载。
 * 画布里的编辑单元把它传给 `allowNavigation`，这类切换不触发离开拦截；去剪辑视图会卸载画布，照常拦截。
 */
export function staysOnEpisodeCanvas(to: string, here: string): boolean {
  const current = new URL(here, "http://episode-view.invalid");
  const target = new URL(to, current);
  return target.pathname === current.pathname && target.searchParams.get(EPISODE_VIEW_PARAM) !== EPISODE_VIEW_EDIT;
}
