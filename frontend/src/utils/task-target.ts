import type { TFunction } from "i18next";
import {
  WORKSPACE_ROUTE_CHARACTERS,
  WORKSPACE_ROUTE_PRODUCTS,
  WORKSPACE_ROUTE_PROPS,
  WORKSPACE_ROUTE_SCENES,
} from "@/app-routes";
import type { ProjectData, TaskItem, WorkspaceNotificationTarget } from "@/types";
import { episodeDisplayName, episodeItemLabel, itemIdsInEpisodeText, type ProjectScripts } from "@/utils/episode-display";

/**
 * Build clickable return-notification targets and readable failure copy from failed tasks.
 *
 * Routes follow task_type:
 * - character/scene/prop/product → assets, without episodes; resource_id is asset names
 * - storyboard/video → episode storyboards, ShotSplitView selects segment IDs
 * - grid → episode grid canvases, navigation only without DOM anchors
 * - reference_video → episode reference units, selected by ReferenceVideoCanvas
 * - image_edit → dispatch by resource_type to corresponding routes above
 *
 * Resolve episodes from task.script_file through projectData.episodes; missing matches return
 * null, retaining notifications without clickable targets (graceful degradation).
 */

const ASSET_ROUTES: Record<"character" | "scene" | "prop" | "product", string> = {
  character: `/${WORKSPACE_ROUTE_CHARACTERS}`,
  scene: `/${WORKSPACE_ROUTE_SCENES}`,
  prop: `/${WORKSPACE_ROUTE_PROPS}`,
  product: `/${WORKSPACE_ROUTE_PRODUCTS}`,
};

/** Asset-page return targets; derivatives locate base-character cards. */
export function assetNotificationTarget(
  assetType: keyof typeof ASSET_ROUTES,
  name: string,
): WorkspaceNotificationTarget {
  return { type: assetType, id: name, route: ASSET_ROUTES[assetType], highlight_style: "flash" };
}

const FAILURE_TEXT_KEYS: Partial<
  Record<TaskItem["task_type"], { key: string; idParam: "id" | "unitId" | "episode" }>
> = {
  storyboard: { key: "storyboard_task_failed", idParam: "id" },
  video: { key: "video_task_failed", idParam: "id" },
  character: { key: "character_task_failed", idParam: "id" },
  scene: { key: "scene_task_failed", idParam: "id" },
  prop: { key: "prop_task_failed", idParam: "id" },
  product: { key: "product_task_failed", idParam: "id" },
  grid: { key: "grid_task_failed", idParam: "id" },
  reference_video: { key: "reference_generation_task_failed", idParam: "unitId" },
  image_edit: { key: "image_edit_task_failed", idParam: "id" },
  text_drama_script_plan: { key: "script_plan_task_failed", idParam: "episode" },
  text_narration_script_plan: { key: "script_plan_task_failed", idParam: "episode" },
  text_reference_script_plan: { key: "script_plan_task_failed", idParam: "episode" },
};

/** Episode-task resource_id values look like `episode-3`; failure copy names episodes by titles. */
const EPISODE_RESOURCE_ID = /^episode-(\d+)$/;

function failureTarget(
  idParam: "id" | "unitId" | "episode",
  task: TaskItem,
  projectData: ProjectData | null,
  scripts: ProjectScripts,
  t: TFunction,
): string {
  const episodes = projectData?.episodes ?? [];
  if (idParam !== "episode") return episodeItemLabel(task.resource_id, scripts, episodes, t);
  const match = EPISODE_RESOURCE_ID.exec(task.resource_id);
  return match ? episodeDisplayName(episodes, Number(match[1]), t) : task.resource_id;
}

/**
 * Normalize script_file: episode metadata always has `scripts/`; enqueue callers supply task/grid rows
 * without consistent formatting guarantees, so strip prefixes before comparing.
 */
export function stripScriptsPrefix(path: string): string {
  return path.replace(/^scripts\//, "");
}

function resolveEpisodeRoute(
  projectData: ProjectData | null,
  scriptFile: string | null,
): string | null {
  if (!projectData || !scriptFile) return null;
  const normalized = stripScriptsPrefix(scriptFile);
  const ep = projectData.episodes.find(
    (e) => stripScriptsPrefix(e.script_file) === normalized,
  );
  return ep ? `/episodes/${ep.episode}` : null;
}

export function buildTaskFailureTarget(
  task: TaskItem,
  projectData: ProjectData | null,
): WorkspaceNotificationTarget | null {
  switch (task.task_type) {
    case "character":
    case "scene":
    case "prop":
    case "product":
      return {
        type: task.task_type,
        id: task.resource_id,
        route: ASSET_ROUTES[task.task_type],
        highlight_style: "flash",
      };
    case "storyboard":
    case "video": {
      const route = resolveEpisodeRoute(projectData, task.script_file);
      return route
        ? { type: "segment", id: task.resource_id, route, highlight_style: "flash" }
        : null;
    }
    case "grid": {
      const route = resolveEpisodeRoute(projectData, task.script_file);
      return route ? { type: "grid", id: task.resource_id, route } : null;
    }
    case "reference_video": {
      const route = resolveEpisodeRoute(projectData, task.script_file);
      return route ? { type: "reference_unit", id: task.resource_id, route } : null;
    }
    case "image_edit": {
      // image_edit shares task_type across character/scene/prop/product/storyboard;
      // actual resource kinds live in resource_type.
      switch (task.resource_type) {
        case "character":
        case "scene":
        case "prop":
        case "product":
          return {
            type: task.resource_type,
            id: task.resource_id,
            route: ASSET_ROUTES[task.resource_type],
            highlight_style: "flash",
          };
        case "storyboard": {
          const route = resolveEpisodeRoute(projectData, task.script_file);
          return route
            ? { type: "segment", id: task.resource_id, route, highlight_style: "flash" }
            : null;
        }
        default:
          return null;
      }
    }
    default:
      return null;
  }
}

/**
 * Maximum rejection-summary characters in notifications. Single-line toasts truncate excess with ellipses;
 * never expand full summaries within those toasts.
 */
const PROVIDER_REASON_MAX_LENGTH = 120;

/**
 * Upstream rejection summaries: raw upstream text separately returned in `provider_rejected` error_params,
 * never translated. Strip entry episode prefixes for display, separately from i18n-template error_message. Other failure codes lack
 * this field; return null.
 */
export function providerReasonOf(task: TaskItem): string | null {
  if (task.error_code !== "provider_rejected") return null;
  const reason = task.error_params?.provider_reason;
  if (typeof reason !== "string") return null;
  const trimmed = itemIdsInEpisodeText(reason.trim());
  return trimmed || null;
}

function truncateProviderReason(reason: string): string {
  // Split code points; UTF-16 code-unit slicing could bisect surrogate pairs, creating lone surrogates.
  const codePoints = [...reason];
  return codePoints.length > PROVIDER_REASON_MAX_LENGTH
    ? `${codePoints.slice(0, PROVIDER_REASON_MAX_LENGTH).join("")}…`
    : reason;
}

/**
 * Failed-task notifications; unknown task_type returns null so callers skip notifications.
 * Refer to entries by Title · 5 (current episode positions); append upstream rejection summaries after task_type-selected copy.
 */
export function describeTaskFailure(
  t: TFunction,
  task: TaskItem,
  projectData: ProjectData | null = null,
  scripts: ProjectScripts = {},
): string | null {
  const reason = task.error_message ?? t("reference_status_failed");
  const config = FAILURE_TEXT_KEYS[task.task_type];
  if (!config) return null;
  const target = failureTarget(config.idParam, task, projectData, scripts, t);
  const message = t(config.key, { [config.idParam]: target, reason });
  const providerReason = providerReasonOf(task);
  if (!providerReason) return message;
  // Render rejection suffix templates separately, never reinserting rendered messages into i18next interpolation:
  // interpolation uses String.replace per match, replacing only first identical placeholders. Literal
  // `{{reason}}` placeholders in messages (possible in raw upstream error_message) would put rejections
  // in incorrect positions.
  const suffix = t("task_failed_provider_reason_suffix", { reason: truncateProviderReason(providerReason) });
  return `${message}${suffix}`;
}
