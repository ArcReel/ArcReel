import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type { TaskItem, TaskStats, TaskStatus } from "@/types";

interface TasksState {
  tasks: TaskItem[];
  stats: TaskStats;
  connected: boolean;
  /** 乐观占用标记，见下方 {@link selectActiveResourceIds} 的乐观占用小节。 */
  optimisticActive: Set<string>;
  /** 乐观占用标记（scriptFile 粒度），见下方 {@link selectHasActiveTaskForScriptFile} 的乐观占用小节。 */
  optimisticActiveScriptFile: Set<string>;

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
  markOptimisticActiveForScriptFile: (
    projectName: string,
    taskType: string,
    scriptFile: string,
  ) => void;
}

const defaultStats: TaskStats = {
  queued: 0, running: 0, cancelling: 0, succeeded: 0, failed: 0, cancelled: 0, total: 0,
};

// 标记发起时尚未有真实行，baseline 取空串——空串小于任何 ISO 时间戳，故首次判定
// 一律视为"尚无真实行"，语义与之前一致；同资源之后再次编辑时 baseline 取当时最新
// 真实行的 updated_at，只有新落地的行（updated_at 大于 baseline）才能让位，旧终态
// 行不会误判为"当前这次"的真实行。
function optimisticKey(
  projectName: string,
  resourceKind: string,
  resourceId: string,
  pendingTaskType: string,
  baselineUpdatedAt: string,
): string {
  return `${projectName}\0${resourceKind}\0${resourceId}\0${pendingTaskType}\0${baselineUpdatedAt}`;
}

// 按当前 tasks 修剪已被真实行取代的乐观占用标记（不新增标记，仅清理）。除标记时机的顺带
// 清理外，也要在每次 setTasks（轮询写回）时执行——store 只保留最近 200 条任务，若真实行
// 落地后被更晚的大量新任务挤出该窗口，仅在“下次再有新标记”时才清理会让这条已完结的旧
// 标记永久残留、误判资源占用中，必须让轮询本身也承担清理职责。
function pruneSupersededOptimisticActive(
  tasks: TaskItem[],
  optimisticActive: ReadonlySet<string>,
): Set<string> {
  const next = new Set<string>();
  for (const key of optimisticActive) {
    const [kProject, kResourceKind, kResourceId, kPendingTaskType, kBaseline = ""] = key.split("\0");
    const superseded = tasks.some(
      (t) =>
        t.project_name === kProject &&
        t.task_type === kPendingTaskType &&
        t.resource_id === kResourceId &&
        taskResourceKind(t) === kResourceKind &&
        t.updated_at > kBaseline,
    );
    if (!superseded) next.add(key);
  }
  return next;
}

function optimisticScriptFileKey(
  projectName: string,
  taskType: string,
  scriptFile: string,
  baselineUpdatedAt: string,
): string {
  return `${projectName}\0${taskType}\0${stripScriptsPrefix(scriptFile)}\0${baselineUpdatedAt}`;
}

// scriptFile 粒度乐观标记的同类修剪，见 {@link pruneSupersededOptimisticActive}。
function pruneSupersededOptimisticActiveScriptFile(
  tasks: TaskItem[],
  optimisticActiveScriptFile: ReadonlySet<string>,
): Set<string> {
  const next = new Set<string>();
  for (const key of optimisticActiveScriptFile) {
    const [kProject, kTaskType, kScriptFile, kBaseline = ""] = key.split("\0");
    const superseded = tasks.some(
      (t) =>
        t.project_name === kProject &&
        t.task_type === kTaskType &&
        t.script_file != null &&
        stripScriptsPrefix(t.script_file) === kScriptFile &&
        t.updated_at > kBaseline,
    );
    if (!superseded) next.add(key);
  }
  return next;
}

export const useTasksStore = create<TasksState>((set) => ({
  tasks: [],
  stats: defaultStats,
  connected: false,
  optimisticActive: new Set(),
  optimisticActiveScriptFile: new Set(),

  setTasks: (tasks) =>
    set((s) => ({
      tasks,
      optimisticActive: pruneSupersededOptimisticActive(tasks, s.optimisticActive),
      optimisticActiveScriptFile: pruneSupersededOptimisticActiveScriptFile(tasks, s.optimisticActiveScriptFile),
    })),
  setStats: (stats) => set({ stats }),
  setConnected: (connected) => set({ connected }),
  markOptimisticActive: (projectName, resourceKind, resourceId, pendingTaskType) =>
    set((s) => {
      let baseline = "";
      for (const t of s.tasks) {
        if (
          t.project_name === projectName &&
          t.task_type === pendingTaskType &&
          t.resource_id === resourceId &&
          taskResourceKind(t) === resourceKind &&
          t.updated_at > baseline
        ) {
          baseline = t.updated_at;
        }
      }

      // 顺带清理已被真实任务行取代的旧标记，避免 Set 在会话周期内无界增长。
      const next = pruneSupersededOptimisticActive(s.tasks, s.optimisticActive);
      next.add(optimisticKey(projectName, resourceKind, resourceId, pendingTaskType, baseline));
      return { optimisticActive: next };
    }),
  markOptimisticActiveForScriptFile: (projectName, taskType, scriptFile) =>
    set((s) => {
      const normalized = stripScriptsPrefix(scriptFile);
      let baseline = "";
      for (const t of s.tasks) {
        if (
          t.project_name === projectName &&
          t.task_type === taskType &&
          t.script_file != null &&
          stripScriptsPrefix(t.script_file) === normalized &&
          t.updated_at > baseline
        ) {
          baseline = t.updated_at;
        }
      }

      const next = pruneSupersededOptimisticActiveScriptFile(s.tasks, s.optimisticActiveScriptFile);
      next.add(optimisticScriptFileKey(projectName, taskType, scriptFile, baseline));
      return { optimisticActiveScriptFile: next };
    }),
}));

// ---------------------------------------------------------------------------
// 派生 selector —— 任务队列两条不变量的单一真相源
//
// 消费点（画布 loading 派生、参考视频单元状态等）此前各自重写两条隐性契约：
//   1.「什么算活跃」——占用与显示是两个谓词：占用判定（isOccupyingStatus）计入
//      cancelling，与后端 dedupe 索引的 ACTIVE_TASK_STATUSES 对齐；显示判定
//      （isActiveStatus）不计 cancelling——取消中的任务不显示为进行中。
//   2.「最新行胜出」——同一 resource 可能有多条任务行：失败后重试是新的 task_id，
//      tasks 由服务端列表整体写入、顺序不保证，故判定时须取 updated_at 最新的一行，
//      重试的新行不被旧失败行遮挡（selectLatestTaskByResource）。
//
// 纯函数版把两条不变量收敛于此、可直接用 vitest 测试；hook 版用 useShallow 比较
// Set/Map 内容，保证内容不变时引用稳定，避免每次渲染返回新集合触发重渲染。
// ---------------------------------------------------------------------------

/** 显示谓词：排队或运行中的任务显示为进行中；cancelling 是收尾中间态，不显示为进行中。 */
export function isActiveStatus(status: TaskStatus): boolean {
  return status === "queued" || status === "running";
}

/**
 * 占用谓词：该状态的任务仍占用其 resource（按钮禁用、占用集判定）。与后端 dedupe
 * 索引的 ACTIVE_TASK_STATUSES 对齐——cancelling 期间 worker 仍可能在写资源文件，
 * 且后端会把同资源的重复提交去重到既有任务上；若前端此时判定空闲，会出现「按钮
 * 可点、提交后没有新任务」的谎报。
 */
export function isOccupyingStatus(status: TaskStatus): boolean {
  return status === "queued" || status === "running" || status === "cancelling";
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
 * （比对不看状态，出现即让位给真实数据）。标记内嵌 baseline（标记发起时刻该资源
 * 已有的最新同类真实行 updated_at）与 resourceKind：同一资源被反复编辑时，若不比对
 * baseline，本次标记会被上一次编辑遗留的旧终态行误判为"已有真实行"而立即失效；
 * 若不比对 resourceKind，不同资源种类之间偶然的 resource_id 相同（如同名角色与场景）
 * 会互相让位。故只有 updated_at 大于 baseline 且 resourceKind 匹配的行才视为"本次
 * 标记等待的真实行"。
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
    if (isOccupyingStatus(task.status)) ids.add(task.resource_id);
  }
  for (const key of optimisticActive) {
    const [kProject, kResourceKind, kResourceId, kPendingTaskType, kBaseline = ""] = key.split("\0");
    if (kProject !== projectName || kResourceKind !== taskType) continue;
    const hasPendingRow = tasks.some(
      (t) =>
        t.project_name === kProject &&
        t.task_type === kPendingTaskType &&
        t.resource_id === kResourceId &&
        taskResourceKind(t) === kResourceKind &&
        t.updated_at > kBaseline,
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
 *
 * 乐观占用：宫格入队请求成功返回到下一次轮询把新 grid 任务行写进 store 之间有 ~3s 空窗，
 * 期间本集在 store 里尚无对应 grid 任务行，分镜编辑入口会误判为空闲、与随后的切割阶段
 * 并发写同一张 storyboard current 图。语义与 {@link selectActiveResourceIds} 的乐观占用
 * 小节一致，但归组键换成 scriptFile（grid 任务无法按 resource_id 归组，见上）。
 */
export function selectHasActiveTaskForScriptFile(
  tasks: TaskItem[],
  taskType: string,
  scriptFile: string,
  projectName: string,
  optimisticActiveScriptFile: ReadonlySet<string> = EMPTY_OPTIMISTIC,
): boolean {
  const normalized = stripScriptsPrefix(scriptFile);
  const hasRealActive = tasks.some(
    (task) =>
      task.project_name === projectName &&
      task.task_type === taskType &&
      task.script_file != null &&
      stripScriptsPrefix(task.script_file) === normalized &&
      isOccupyingStatus(task.status),
  );
  if (hasRealActive) return true;

  for (const key of optimisticActiveScriptFile) {
    const [kProject, kTaskType, kScriptFile, kBaseline = ""] = key.split("\0");
    if (kProject !== projectName || kTaskType !== taskType || kScriptFile !== normalized) continue;
    const hasPendingRow = tasks.some(
      (t) =>
        t.project_name === kProject &&
        t.task_type === kTaskType &&
        t.script_file != null &&
        stripScriptsPrefix(t.script_file) === kScriptFile &&
        t.updated_at > kBaseline,
    );
    if (!hasPendingRow) return true;
  }
  return false;
}

/** hook 版 {@link selectHasActiveTaskForScriptFile}；scriptFile/projectName 缺失时返回 false。 */
export function useHasActiveTaskForScriptFile(
  taskType: string,
  scriptFile: string | undefined | null,
  projectName: string | undefined | null,
): boolean {
  return useTasksStore((s) =>
    scriptFile && projectName
      ? selectHasActiveTaskForScriptFile(
          s.tasks,
          taskType,
          scriptFile,
          projectName,
          s.optimisticActiveScriptFile,
        )
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
