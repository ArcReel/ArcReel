import type { TFunction } from "i18next";
import type { ProjectData, TaskItem, WorkspaceNotificationTarget } from "@/types";

/**
 * 由失败任务构建可点击回跳的通知 target，以及人类可读的失败文案。
 *
 * 回跳路由按 task_type 区分：
 * - character/scene/prop → 资产页（不需要剧集），resource_id 即资产名
 * - storyboard/video → 对应剧集的分镜（ShotSplitView 按 segment id 选中）
 * - grid → 对应剧集的宫格画布（导航即回跳，无 DOM 锚点）
 * - reference_video → 对应剧集的参考单元（ReferenceVideoCanvas 选中 unit）
 *
 * 剧集路由由 task.script_file 反查 projectData.episodes 得到；查不到时返回
 * null，让通知仍然推送、仅不可点击（优雅降级）。
 */

const ASSET_ROUTES: Record<string, string> = {
  character: "/characters",
  scene: "/scenes",
  prop: "/props",
};

const FAILURE_TEXT_KEYS: Record<string, string> = {
  storyboard: "storyboard_task_failed",
  video: "video_task_failed",
  character: "character_task_failed",
  scene: "scene_task_failed",
  prop: "prop_task_failed",
  grid: "grid_task_failed",
};

function stripScriptsPrefix(path: string): string {
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
    default:
      return null;
  }
}

/**
 * 失败任务的通知文案。未知 task_type 返回 null（调用方据此跳过推送）。
 */
export function describeTaskFailure(t: TFunction, task: TaskItem): string | null {
  const reason = task.error_message ?? t("reference_status_failed");
  if (task.task_type === "reference_video") {
    return t("reference_generation_task_failed", { unitId: task.resource_id, reason });
  }
  const key = FAILURE_TEXT_KEYS[task.task_type];
  if (!key) return null;
  return t(key, { id: task.resource_id, reason });
}
