import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type { TaskItem, TaskStats, TaskStatus } from "@/types";

interface TasksState {
  tasks: TaskItem[];
  stats: TaskStats;
  connected: boolean;
  /** 乐观占用标记，见下方 {@link selectActiveResourceIds} 的乐观占用小节。 */
  optimisticActive: Set<string>;

  // Actions
  setTasks: (tasks: TaskItem[]) => void;
  setStats: (stats: TaskStats) => void;
  setConnected: (connected: boolean) => void;
  markOptimisticActive: (
    projectName: string,
    resourceKind: string,
    resourceId: string,
    pendingTaskType: string,
  ) => void;
}

const defaultStats: TaskStats = {
  queued: 0, running: 0, cancelling: 0, succeeded: 0, failed: 0, cancelled: 0, total: 0,
};

function optimisticKey(
  projectName: string,
  resourceKind: string,
  resourceId: string,
  pendingTaskType: string,
): string {
  return `${projectName}\0${resourceKind}\0${resourceId}\0${pendingTaskType}`;
}

export const useTasksStore = create<TasksState>((set) => ({
  tasks: [],
  stats: defaultStats,
  connected: false,
  optimisticActive: new Set(),

  setTasks: (tasks) => set({ tasks }),
  setStats: (stats) => set({ stats }),
  setConnected: (connected) => set({ connected }),
  markOptimisticActive: (projectName, resourceKind, resourceId, pendingTaskType) =>
    set((s) => {
      // 顺带清理已被真实任务行取代的旧标记，避免 Set 在会话周期内无界增长。
      const next = new Set<string>();
      for (const key of s.optimisticActive) {
        const [kProject, , kResourceId, kPendingTaskType] = key.split("\0");
        const superseded = s.tasks.some(
          (t) =>
            t.project_name === kProject && t.task_type === kPendingTaskType && t.resource_id === kResourceId,
        );
        if (!superseded) next.add(key);
      }
      next.add(optimisticKey(projectName, resourceKind, resourceId, pendingTaskType));
      return { optimisticActive: next };
    }),
}));

// ---------------------------------------------------------------------------
// 派生 selector —— 任务队列两条不变量的单一真相源
//
// 消费点（画布 loading 派生、参考视频单元状态等）此前各自重写两条隐性契约：
//   1.「什么算活跃」——排队或运行中的任务占用其 resource（isActiveStatus）。
//   2.「最新行胜出」——同一 resource 可能有多条任务行：失败后重试是新的 task_id，
//      tasks 由服务端列表整体写入、顺序不保证，故判定时须取 updated_at 最新的一行，
//      重试的新行不被旧失败行遮挡（selectLatestTaskByResource）。
//
// 纯函数版把两条不变量收敛于此、可直接用 vitest 测试；hook 版用 useShallow 比较
// Set/Map 内容，保证内容不变时引用稳定，避免每次渲染返回新集合触发重渲染。
// ---------------------------------------------------------------------------

/** 不变量 1：排队或运行中的任务视为占用其 resource。 */
export function isActiveStatus(status: TaskStatus): boolean {
  return status === "queued" || status === "running";
}

/** 终态：任务生命周期末端，不再占用 resource。 */
export function isTerminalStatus(status: TaskStatus): boolean {
  return status === "succeeded" || status === "failed" || status === "cancelled";
}

/**
 * 任务占用的「资源种类」。除 image_edit 外，task_type 本身即资源种类；image_edit 跨
 * character/scene/prop/product/storyboard 共用一个 task_type，真正的种类在 resource_type，
 * 故按 resource_type 归槽——编辑任务与同资源的生成任务落入同一占用集、彼此互斥。
 */
export function taskResourceKind(task: TaskItem): string {
  return task.task_type === "image_edit" ? (task.resource_type ?? "") : task.task_type;
}

/**
 * 不变量 2：按 resource_id 归并任务，同一 resource 多行时取 updated_at 最新的一行。
 * 可选按 projectName / taskType 预筛；store 不保证顺序，故显式比较 updated_at。
 */
export function selectLatestTaskByResource(
  tasks: TaskItem[],
  filter: { projectName?: string; taskType?: string } = {},
): Map<string, TaskItem> {
  const latest = new Map<string, TaskItem>();
  for (const task of tasks) {
    if (filter.projectName !== undefined && task.project_name !== filter.projectName) continue;
    if (filter.taskType !== undefined && taskResourceKind(task) !== filter.taskType) continue;
    const prev = latest.get(task.resource_id);
    if (!prev || task.updated_at > prev.updated_at) latest.set(task.resource_id, task);
  }
  return latest;
}

/**
 * 命中 taskType + projectName 且「最新行」处于活跃态的 resource_id 集合。
 * 「最新行胜出」按 (resource_id, task.task_type) 二级键分别归并——同一原生 task_type
 * 内，重试的新 running/queued 行不被同 resource 的旧 failed 行盖住；但 image_edit 与
 * 其目标资源的生成任务是两个独立 task_type（仅通过 taskResourceKind 共享同一占用槽），
 * 后端并无互斥保证，二者可能真实并存。若仍按单一「最新行」判定，较新落地的编辑终态
 * （成功/失败）会掩盖仍在运行的生成任务（或反之），导致资源被误判为空闲。故按各自
 * task_type 分别取最新行，再在任一 task_type 的最新行活跃时即计入占用。
 *
 * 乐观占用：入队请求成功返回到 `useTasksSSE` 下一次轮询把新任务行写进 store 之间有
 * ~3s 空窗（轮询间隔），期间该 resource 在 store 里还没有对应任务行、判定为空闲。
 * image_edit 与其目标资源共用占用槽，是第一个会在此空窗内与「本资源另一 task_type」
 * 并发提交的场景（同 task_type 的并发提交已被后端 dedupe 索引拦下，见
 * `idx_tasks_dedupe_active`，但该索引以 task_type 为键的一部分，不拦跨 task_type 并发）。
 * `optimisticActive` 由提交方（如 ImageEditButton）在提交成功后立即标记，此处按
 * (projectName, taskType) 过滤后并入占用集，直到标记所等待的真实任务行出现为止
 * （比对不看状态，出现即让位给真实数据）。
 */
export function selectActiveResourceIds(
  tasks: TaskItem[],
  taskType: string,
  projectName: string,
  optimisticActive: ReadonlySet<string> = EMPTY_OPTIMISTIC,
): Set<string> {
  const latestByResourceAndTaskType = new Map<string, TaskItem>();
  for (const task of tasks) {
    if (task.project_name !== projectName) continue;
    if (taskResourceKind(task) !== taskType) continue;
    const key = `${task.resource_id}\0${task.task_type}`;
    const prev = latestByResourceAndTaskType.get(key);
    if (!prev || task.updated_at > prev.updated_at) latestByResourceAndTaskType.set(key, task);
  }
  const ids = new Set<string>();
  for (const task of latestByResourceAndTaskType.values()) {
    if (isActiveStatus(task.status)) ids.add(task.resource_id);
  }
  for (const key of optimisticActive) {
    const [kProject, kResourceKind, kResourceId, kPendingTaskType] = key.split("\0");
    if (kProject !== projectName || kResourceKind !== taskType) continue;
    const hasPendingRow = tasks.some(
      (t) => t.project_name === kProject && t.task_type === kPendingTaskType && t.resource_id === kResourceId,
    );
    if (!hasPendingRow) ids.add(kResourceId);
  }
  return ids;
}

const EMPTY_OPTIMISTIC: ReadonlySet<string> = new Set();

// 与 task-target.ts 的 stripScriptsPrefix 同一归一化规则：episode 元数据的 script_file
// 固定带 `scripts/` 前缀（见 ProjectManager._apply_episode_sync），但任务行的 script_file
// 由各入队调用方各自传入——router 直传 webui 表单值，Agent/SDK 工具经 validate_script_filename
// 强制裸文件名，两者格式不保证一致。此处不依赖调用方预先裁剪，自行归一化后再比较。
function stripScriptsPrefix(path: string): string {
  return path.replace(/^scripts\//, "");
}

/**
 * 是否存在指定 scriptFile 下、taskType 类型的活跃任务。不做「最新行胜出」归并——
 * 存在即算，用于粗粒度剧集级占用判定：grid 任务的 resource_id 是 grid_id 而非
 * 分镜 segment_id，无法归入 selectActiveResourceIds 的按资源判定；但 grid 切割阶段
 * 会覆写本集内多个分镜的 storyboard 文件，故按 scriptFile 判定「本集是否有宫格任务
 * 在跑」，用于禁用宫格模式下的分镜编辑入口，避免编辑与切割并发写同一文件。
 */
export function selectHasActiveTaskForScriptFile(
  tasks: TaskItem[],
  taskType: string,
  scriptFile: string,
  projectName: string,
): boolean {
  const normalized = stripScriptsPrefix(scriptFile);
  return tasks.some(
    (task) =>
      task.project_name === projectName &&
      task.task_type === taskType &&
      task.script_file != null &&
      stripScriptsPrefix(task.script_file) === normalized &&
      isActiveStatus(task.status),
  );
}

/** hook 版 {@link selectHasActiveTaskForScriptFile}；scriptFile/projectName 缺失时返回 false。 */
export function useHasActiveTaskForScriptFile(
  taskType: string,
  scriptFile: string | undefined | null,
  projectName: string | undefined | null,
): boolean {
  return useTasksStore((s) =>
    scriptFile && projectName
      ? selectHasActiveTaskForScriptFile(s.tasks, taskType, scriptFile, projectName)
      : false,
  );
}

// projectName 缺失时复用同一空集，保证 hook 引用稳定。
const EMPTY_ACTIVE_IDS: Set<string> = new Set();

/** hook 版 {@link selectActiveResourceIds}；projectName 缺失时返回稳定空集。 */
export function useActiveResourceIds(
  taskType: string,
  projectName: string | undefined | null,
): Set<string> {
  return useTasksStore(
    useShallow((s) =>
      projectName
        ? selectActiveResourceIds(s.tasks, taskType, projectName, s.optimisticActive)
        : EMPTY_ACTIVE_IDS,
    ),
  );
}

/** hook 版 {@link selectLatestTaskByResource}，按 project + type 预筛。 */
export function useLatestTasksByResource(
  projectName: string,
  taskType: string,
): Map<string, TaskItem> {
  return useTasksStore(
    useShallow((s) => selectLatestTaskByResource(s.tasks, { projectName, taskType })),
  );
}
