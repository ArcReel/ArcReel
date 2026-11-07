import type { TFunction } from "i18next";

import { episodesViewPath } from "@/components/canvas/episodes/episodes-view-model";
import type { EpisodeMeta } from "@/types";
import type { WorkflowNextAction, WorkflowStatus } from "@/types/workflow";
import { episodeDisplayName, episodePosition, type EpisodeLedger } from "@/utils/episode-display";

/**
 * Header status-bar Next step presentation: projected from project production state `next_action` and `next_alternatives`;
 * the UI never infers next steps itself.
 *
 * Three button kinds: `nav` navigates to workbench locations that can complete steps; `agent` prefills requests for users to confirm sending;
 * `ai` submits directly (currently only AI episode planning). Actions without direct Web entry points offer only `agent`.
 */
export type GuideButton =
  | { kind: "nav"; label: string; to: string }
  | { kind: "agent"; label: string; prefill: string }
  | { kind: "ai"; label: string; action: "plan_episodes" };

export interface ProjectNextGuide {
  /** Phrase following Next step in the status bar right segment. */
  title: string;
  /** One explanatory sentence in the popover. */
  detail: string;
  /** Whether to include extra-instruction inputs; these instructions enter only messages passed to Agent. */
  instruction: boolean;
  primary: GuideButton[];
  alternatives: GuideButton[];
  /** Target episode ID for next steps; null for project-level actions. */
  episodeId: number | null;
}

/** Imperative next-action phrase, sharing translations with episode panels. */
export function actionPhrase(t: TFunction, action: WorkflowNextAction["type"]): string {
  return t(`workflow:action_${action}`, { defaultValue: t("workflow:action_unknown") });
}

function episodeIdArg(action: WorkflowNextAction): number | null {
  const value = action.args.episode_id;
  return typeof value === "number" ? value : null;
}

function alternativeButton(t: TFunction, action: WorkflowNextAction): GuideButton | null {
  if (action.type === "create_episode") {
    return { kind: "nav", label: t("dashboard:guide_alt_create_episode"), to: episodesViewPath({ create: true }) };
  }
  if (action.type === "none") return null;
  return {
    kind: "agent",
    label: t("dashboard:guide_alt_agent", { step: actionPhrase(t, action.type) }),
    prefill: t("dashboard:guide_prefill_generic", { step: actionPhrase(t, action.type) }),
  };
}

/**
 * Project next steps. Return null for no step (`none`) or project-data upgrade failures (handled by migration status bars).
 * Queued/running episode planning (`planningActive`) replaces next steps with Episode planning in progress.
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
  // Episode alternatives (start from scratch, add episode source, etc.) have local panel entries; headers do not rewrite them as Hand to Agent.
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
    // Short films contain one episode: navigate to the sole ledger episode without assuming its ID.
    const adEpisodeId = episodeIdArg(action) ?? episodes[0]?.episode ?? null;
    if (action.type === "collect_project_input") {
      // Missing creative ideas/products prevent script generation: fill these in on overview pages, matching episode-panel reminder destinations.
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

/** Agent messages: append nonempty extra instructions on a new line. */
export function withInstruction(t: TFunction, prefill: string, instruction: string): string {
  const text = instruction.trim();
  return text ? `${prefill}\n${t("dashboard:guide_prefill_instruction", { instruction: text })}` : prefill;
}

/** Episode artifacts include storyboards or videos older than current contents. */
export function episodeNeedsUpdate(episode: Pick<EpisodeMeta, "storyboards" | "videos">): boolean {
  return (episode.storyboards?.stale ?? 0) + (episode.videos?.stale ?? 0) > 0;
}
