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
 * - image_edit → 按 resource_type 转发到上述对应路由；product 暂无路由（与既有
 *   product 生成任务缺口一致），仅推送文案不可点击
 *
 * 剧集路由由 task.script_file 反查 projectData.episodes 得到；查不到时返回
 * null，让通知仍然推送、仅不可点击（优雅降级）。
 */

const ASSET_ROUTES: Record<"character" | "scene" | "prop", string> = {
  character: "/characters",
  scene: "/scenes",
  prop: "/props",
};

const FAILURE_TEXT_KEYS: Partial<
  Record<TaskItem["task_type"], { key: string; idParam: "id" | "unitId" }>
> = {
  storyboard: { key: "storyboard_task_failed", idParam: "id" },
  video: { key: "video_task_failed", idParam: "id" },
  character: { key: "character_task_failed", idParam: "id" },
  scene: { key: "scene_task_failed", idParam: "id" },
  prop: { key: "prop_task_failed", idParam: "id" },
  grid: { key: "grid_task_failed", idParam: "id" },
  reference_video: { key: "reference_generation_task_failed", idParam: "unitId" },
  image_edit: { key: "image_edit_task_failed", idParam: "id" },
};

/**
 * 归一化 script_file：episode 元数据固定带 `scripts/` 前缀，任务行与 grid 记录
 * 由各入队调用方自行传入、格式不保证一致，比较前统一剥前缀。
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
      // image_edit 跨 character/scene/prop/product/storyboard 共用 task_type，真正
      // 的资源种类在 resource_type；product 目前无对应 WorkspaceFocusTarget 路由
      // （与既有 product 生成任务的通知目标缺口一致），优雅降级为不可点击。
      switch (task.resource_type) {
        case "character":
        case "scene":
        case "prop":
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
 * 通知文案里拒因摘要的最大字符数。通知是一行 toast，超出部分截断并省略号收尾，
 * 完整摘要仍可在任务面板（TaskHud）读到。
 */
const PROVIDER_REASON_MAX_LENGTH = 120;

/**
 * 上游拒因摘要：后端在 `provider_rejected` 的 error_params 里单独回传的上游原文，
 * 不参与翻译，与走 i18n 模板的 error_message 分开渲染。级联失败等其它失败码没有
 * 这个字段，返回 null。
 */
export function providerReasonOf(task: TaskItem): string | null {
  if (task.error_code !== "provider_rejected") return null;
  const reason = task.error_params?.provider_reason;
  if (typeof reason !== "string") return null;
  const trimmed = reason.trim();
  return trimmed || null;
}

function truncateProviderReason(reason: string): string {
  // 按码点切分：直接对 UTF-16 code unit 切片会从代理对中间断开，产出孤立代理。
  const codePoints = [...reason];
  return codePoints.length > PROVIDER_REASON_MAX_LENGTH
    ? `${codePoints.slice(0, PROVIDER_REASON_MAX_LENGTH).join("")}…`
    : reason;
}

/**
 * 失败任务的通知文案。未知 task_type 返回 null（调用方据此跳过推送）。
 * 有上游拒因摘要时追加在按 task_type 选出的文案之后。
 */
export function describeTaskFailure(t: TFunction, task: TaskItem): string | null {
  const reason = task.error_message ?? t("reference_status_failed");
  const config = FAILURE_TEXT_KEYS[task.task_type];
  if (!config) return null;
  const message = t(config.key, { [config.idParam]: task.resource_id, reason });
  const providerReason = providerReasonOf(task);
  if (!providerReason) return message;
  // 拒因后缀单独取模板再拼接，不把已渲染的 message 当插值变量传回 i18next：插值
  // 对每个 match 走 String.replace，只命中首个同名占位符，message 里若含
  // `{{reason}}` 这类字面占位符（error_message 是上游原文，可能包含），拒因会落到
  // 错误位置。
  const suffix = t("task_failed_provider_reason_suffix", { reason: truncateProviderReason(providerReason) });
  return `${message}${suffix}`;
}
