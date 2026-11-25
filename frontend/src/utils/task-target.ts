import type { TFunction } from "i18next";
import {
  WORKSPACE_ROUTE_CHARACTERS,
  WORKSPACE_ROUTE_PRODUCTS,
  WORKSPACE_ROUTE_PROPS,
  WORKSPACE_ROUTE_SCENES,
} from "@/app-routes";
import type { ItemLocation, ProjectData, TaskItem, WorkspaceNotificationTarget } from "@/types";
import {
  episodeDisplayName,
  episodeItemLabel,
  episodeItemRefLabel,
  formatEpisodeItemLocation,
  itemIdsInEpisodeText,
  type ProjectScripts,
} from "@/utils/episode-display";


const ASSET_ROUTES: Record<"character" | "scene" | "prop" | "product", string> = {
  character: `/${WORKSPACE_ROUTE_CHARACTERS}`,
  scene: `/${WORKSPACE_ROUTE_SCENES}`,
  prop: `/${WORKSPACE_ROUTE_PROPS}`,
  product: `/${WORKSPACE_ROUTE_PRODUCTS}`,
};

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

const EPISODE_RESOURCE_ID = /^episode-(\d+)$/;

function failureTarget(
  idParam: "id" | "unitId" | "episode",
  task: TaskItem,
  projectData: ProjectData | null,
  scripts: ProjectScripts,
  t: TFunction,
  locations?: ReadonlyMap<string, ItemLocation>,
): string {
  const episodes = projectData?.episodes ?? [];
  if (idParam === "episode") {
    const match = EPISODE_RESOURCE_ID.exec(task.resource_id);
    return match ? episodeDisplayName(episodes, Number(match[1]), t) : task.resource_id;
  }
  if (typeof task.position === "number") {
    return formatEpisodeItemLocation(
      task.item_id ?? task.resource_id,
      {
        episode: task.episode ?? null,
        item_id: task.item_id ?? task.resource_id,
        position: task.position,
        status: "found",
      },
      episodes,
      t,
    );
  }
  if (task.resource_ref) {
    return episodeItemRefLabel(task.resource_id, task.resource_ref, t, episodes);
  }
  return episodeItemLabel(task.resource_id, scripts, episodes, t, locations);
}

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

const PROVIDER_REASON_MAX_LENGTH = 120;

export function providerReasonOf(task: TaskItem): string | null {
  if (task.error_code !== "provider_rejected") return null;
  const reason = task.error_params?.provider_reason;
  if (typeof reason !== "string") return null;
  const trimmed = itemIdsInEpisodeText(reason.trim());
  return trimmed || null;
}

function truncateProviderReason(reason: string): string {
  const codePoints = [...reason];
  return codePoints.length > PROVIDER_REASON_MAX_LENGTH
    ? `${codePoints.slice(0, PROVIDER_REASON_MAX_LENGTH).join("")}…`
    : reason;
}

export function describeTaskFailure(
  t: TFunction,
  task: TaskItem,
  projectData: ProjectData | null = null,
  scripts: ProjectScripts = {},
  locations?: ReadonlyMap<string, ItemLocation>,
): string | null {
  const reason =
    task.error_code === "text_wait_timeout"
      ? t("errors:text_wait_timeout")
      : (task.error_message ?? t("reference_status_failed"));
  const config = FAILURE_TEXT_KEYS[task.task_type];
  if (!config) return null;
  const target = failureTarget(config.idParam, task, projectData, scripts, t, locations);
  const message = t(config.key, { [config.idParam]: target, reason });
  const providerReason = providerReasonOf(task);
  if (!providerReason) return message;
  // 拒因后缀单独取模板再拼接，不把已渲染的 message 当插值变量传回 i18next：插值
  // 对每个 match 走 String.replace，只命中首个同名占位符，message 里若含
  // `{{reason}}` 这类字面占位符（error_message 是上游原文，可能包含），拒因会落到
  // 错误位置。
  const suffix = t("task_failed_provider_reason_suffix", { reason: truncateProviderReason(providerReason) });
  return `${message}${suffix}`;
}
