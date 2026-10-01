import type { TFunction } from "i18next";

import { episodesViewPath } from "@/components/canvas/episodes/episodes-view-model";
import type { EpisodeMeta } from "@/types";
import type { WorkflowNextAction, WorkflowStatus } from "@/types/workflow";
import { episodeDisplayName, episodePosition, type EpisodeLedger } from "@/utils/episode-display";

/**
 * 顶栏状态条「下一步」的呈现形状：由项目层制作状态的 `next_action` 与 `next_alternatives` 投影而来，
 * 界面不自行推断下一步。
 *
 * 按钮分三种：`nav` 跳到工作台里能完成这一步的位置；`agent` 把请求预填进 Agent 输入框，由用户确认发送；
 * `ai` 直接提交这一步（目前只有 AI 规划分集）。还没有 Web 直接调用入口的动作只给 `agent`。
 */
export type GuideButton =
  | { kind: "nav"; label: string; to: string }
  | { kind: "agent"; label: string; prefill: string }
  | { kind: "ai"; label: string; action: "plan_episodes" };

export interface ProjectNextGuide {
  /** 状态条右段「下一步」后面的短语。 */
  title: string;
  /** 弹层里的一句说明。 */
  detail: string;
  /** 是否带附加指令输入框；附加指令只写进交给 Agent 的消息。 */
  instruction: boolean;
  primary: GuideButton[];
  alternatives: GuideButton[];
  /** 下一步落在哪一集（集 ID）；项目层动作为 null。 */
  episodeId: number | null;
}

/** 下一步动作的祈使短语，与集页面板同一套译文。 */
export function actionPhrase(t: TFunction, action: WorkflowNextAction["type"]): string {
  return t(`workflow:action_${action}`, { defaultValue: t("workflow:action_unknown") });
}

function episodeIdArg(action: WorkflowNextAction): number | null {
  const value = action.args.episode_id;
  return typeof value === "number" ? value : null;
}

function alternativeButton(t: TFunction, action: WorkflowNextAction): GuideButton | null {
  if (action.type === "create_episode") {
    return {
      kind: "agent",
      label: t("dashboard:guide_alt_create_episode"),
      prefill: t("dashboard:guide_prefill_create_episode"),
    };
  }
  if (action.type === "none") return null;
  return {
    kind: "agent",
    label: t("dashboard:guide_alt_agent", { step: actionPhrase(t, action.type) }),
    prefill: t("dashboard:guide_prefill_generic", { step: actionPhrase(t, action.type) }),
  };
}

/**
 * 项目层的下一步。没有下一步（`none`）或项目数据升级失败（由状态条的迁移形态接管）时返回 null。
 * 有分集规划在排队或执行时（`planningActive`），下一步让位给「分集规划进行中」。
 */
export function projectNextGuide(
  t: TFunction,
  status: WorkflowStatus,
  episodes: EpisodeLedger,
  { planningActive = false }: { planningActive?: boolean } = {},
): ProjectNextGuide | null {
  const action = status.next_action;
  if (action.type === "retry_project_migration") return null;
  if (planningActive && status.project.content_mode !== "ad") {
    return {
      title: t("dashboard:guide_planning_running_title"),
      detail: t("dashboard:guide_planning_running_detail"),
      instruction: false,
      primary: [{ kind: "nav", label: t("dashboard:guide_view_planning_progress"), to: episodesViewPath() }],
      alternatives: [],
      episodeId: null,
    };
  }
  if (action.type === "none") return null;
  // 落在某一集的备选（从空白开始、补充集原文等）在集页面板就地给出入口，顶栏不改写成「交给 Agent」
  const alternatives = status.next_alternatives
    .filter((alternative) => episodeIdArg(alternative) === null)
    .map((alternative) => alternativeButton(t, alternative))
    .filter((button): button is GuideButton => button !== null);
  const agent = (prefill: string): GuideButton => ({
    kind: "agent",
    label: t("dashboard:guide_hand_to_agent"),
    prefill,
  });

  if (status.project.content_mode === "ad") {
    // 短片只有一集：跳到账本里唯一的那一集，不假定它的集 ID。
    const adEpisodeId = episodeIdArg(action) ?? episodes[0]?.episode ?? null;
    if (action.type === "collect_project_input") {
      // 缺创作灵感和商品时生成脚本不可做，先去概览页填写，与集页面板的提醒同一去处
      return {
        title: t("workflow:next_title_generate_script"),
        detail: t("workflow:hint_fill_brief"),
        instruction: false,
        primary: [{ kind: "nav", label: t("workflow:act_fill_brief"), to: "/" }],
        alternatives: [],
        episodeId: adEpisodeId,
      };
    }
    return {
      title: actionPhrase(t, action.type),
      detail: t("dashboard:guide_ad_detail"),
      instruction: false,
      primary:
        adEpisodeId === null
          ? []
          : [{ kind: "nav", label: t("dashboard:guide_go_ad"), to: `/episodes/${adEpisodeId}` }],
      alternatives: [],
      episodeId: adEpisodeId,
    };
  }

  const episodeId = episodeIdArg(action);
  if (episodeId !== null) {
    const position = episodePosition(episodes, episodeId);
    const name = episodeDisplayName(episodes, episodeId, t);
    return {
      title:
        position === null
          ? t("dashboard:guide_continue_named_episode", { name })
          : t("dashboard:guide_continue_episode", { position }),
      detail: t("dashboard:guide_episode_detail", { name, step: actionPhrase(t, action.type) }),
      instruction: false,
      primary: [
        {
          kind: "nav",
          label:
            position === null
              ? t("dashboard:guide_go_named_episode", { name })
              : t("dashboard:guide_go_episode", { position }),
          to: `/episodes/${episodeId}`,
        },
      ],
      alternatives,
      episodeId,
    };
  }

  const planned = (status.content?.episode_count ?? 0) > 0;
  switch (action.type) {
    case "plan_episodes":
      return {
        title: planned ? t("dashboard:guide_plan_continue_title") : t("dashboard:guide_plan_title"),
        detail: planned ? t("dashboard:guide_plan_continue_detail") : t("dashboard:guide_plan_detail"),
        instruction: true,
        primary: [
          agent(planned ? t("dashboard:guide_prefill_plan_continue") : t("dashboard:guide_prefill_plan")),
          {
            kind: "ai",
            label: planned ? t("dashboard:episode_planning_continue") : t("dashboard:episode_planning_start"),
            action: "plan_episodes",
          },
        ],
        alternatives,
        episodeId: null,
      };
    case "reset_episode_planning":
      return {
        title: t("dashboard:guide_replan_title"),
        detail: t("dashboard:guide_replan_detail"),
        instruction: false,
        primary: [agent(t("dashboard:guide_prefill_replan"))],
        alternatives,
        episodeId: null,
      };
    case "collect_project_input":
      return {
        title: t("dashboard:guide_upload_title"),
        detail: t("dashboard:guide_upload_detail"),
        instruction: false,
        primary: [{ kind: "nav", label: t("dashboard:guide_upload_title"), to: episodesViewPath({ upload: "whole_source" }) }],
        alternatives,
        episodeId: null,
      };
    default:
      return {
        title: actionPhrase(t, action.type),
        detail: t("workflow:next_step", { step: actionPhrase(t, action.type) }),
        instruction: false,
        primary: [agent(t("dashboard:guide_prefill_generic", { step: actionPhrase(t, action.type) }))],
        alternatives,
        episodeId: null,
      };
  }
}

/** 交给 Agent 的消息：附加指令非空时另起一行附上。 */
export function withInstruction(t: TFunction, prefill: string, instruction: string): string {
  const text = instruction.trim();
  return text ? `${prefill}\n${t("dashboard:guide_prefill_instruction", { instruction: text })}` : prefill;
}

/** 集的产物里有比当前内容旧的（分镜图或视频）。 */
export function episodeNeedsUpdate(episode: Pick<EpisodeMeta, "storyboards" | "videos">): boolean {
  return (episode.storyboards?.stale ?? 0) + (episode.videos?.stale ?? 0) > 0;
}
