import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type { TaskItem, TaskStats, TaskStatus } from "@/types";

/**
 * 一次入队提交的乐观占用句柄。由入队动作层在**请求发出前**取得，请求结束时二选一兑现：
 *
 * - `settle(taskIds)` —— 后端已建（或去重命中）任务行，标记转为等待这些 task_id 落库；
 *   传空数组表示本次没有产生任何任务行，等同 `rollback()`（否则标记永远等不到真实行）。
 * - `rollback()` —— 请求失败，撤销标记。
 *
 * 未兑现前标记处于「在途」态，不被任何轮询写回清除——这段正是请求往返窗口，
 * 各调用方过去要自备 in-flight ref 才能覆盖。
 */
export interface OptimisticHandle {
  settle: (taskIds: string[]) => void;
  rollback: () => void;
}

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
  beginOptimisticActive: (
    projectName: string,
    resourceKind: string,
    resourceId: string,
    pendingTaskType: string,
  ) => OptimisticHandle;
  beginOptimisticActiveForScriptFile: (
    projectName: string,
    taskType: string,
    scriptFile: string,
  ) => OptimisticHandle;
}

export const defaultTaskStats: TaskStats = {
  queued: 0, running: 0, cancelling: 0, succeeded: 0, failed: 0, cancelled: 0, total: 0,
};

// ---------------------------------------------------------------------------
// 乐观占用标记的编码与让位判定
//
// 标记以字符串 key 存于 Set，尾部两段是「标记序号」与「已兑现的 task_id 列表」：
//
//   资源粒度   project \0 resourceKind \0 resourceId \0 pendingTaskType \0 seq \0 taskIds
//   scriptFile project \0 taskType \0 scriptFile \0 seq \0 taskIds
//
// seq 让同一资源上的两次并发提交各自可独立回滚；taskIds 为空串即「在途」——请求已发出、
// 后端任务 id 未知，此时标记不可被任何轮询写回清除。两种粒度的字段数不同，但 taskIds
// 一律是最后一段，故让位判定只需 {@link markTaskIds} 一个解析口径，加字段不会错位。
//
// 让位判定看 task_id 而非时间戳：只有本次提交自己的任务行**全部**出现在 tasks 里，标记
// 才交还给真实数据；已落库的 id 逐轮从 key 里扣除，故不要求它们在同一个快照里同时出现。
// 这条判据同时堵住两个时序洞：
//   1. 终态先到不再永久残留——去重命中既有任务时后端返回的是那条既有行的 task_id，
//      它已在（或即将在）tasks 里，标记随即让位；按时间戳比较则会因该行 updated_at
//      不大于标记发起时的基线而永不让位。
//   2. 晚到的旧轮询结果不再覆盖较新的标记——旧快照里没有本次的 task_id，判定不成立。
// ---------------------------------------------------------------------------

/** 已兑现 task_id 之间的分隔符；与字段分隔符 \0 区分开，便于解析。 */
const TASK_ID_SEP = "\u0001";

let optimisticSeq = 0;

function encodeTaskIds(taskIds: readonly string[]): string {
  return taskIds.join(TASK_ID_SEP);
}

/** 取标记 key 末段的 taskIds；在途标记（末段为空串）返回空数组。 */
function markTaskIds(key: string): string[] {
  const encoded = key.split("\0").at(-1);
  return encoded ? encoded.split(TASK_ID_SEP) : [];
}

function optimisticKey(
  projectName: string,
  resourceKind: string,
  resourceId: string,
  pendingTaskType: string,
  seq: number,
  taskIds: readonly string[],
): string {
  return `${projectName}\0${resourceKind}\0${resourceId}\0${pendingTaskType}\0${seq}\0${encodeTaskIds(taskIds)}`;
}

function optimisticScriptFileKey(
  projectName: string,
  taskType: string,
  scriptFile: string,
  seq: number,
  taskIds: readonly string[],
): string {
  return `${projectName}\0${taskType}\0${stripScriptsPrefix(scriptFile)}\0${seq}\0${encodeTaskIds(taskIds)}`;
}

/** 把 key 末段的 taskIds 换成 `taskIds`，其余字段原样保留。 */
function withTaskIds(key: string, taskIds: readonly string[]): string {
  return key.slice(0, key.lastIndexOf("\0") + 1) + encodeTaskIds(taskIds);
}

/** 本次快照里已落库的 task_id。 */
function landedTaskIds(tasks: TaskItem[]): Set<string> {
  return new Set(tasks.map((t) => t.task_id));
}

/**
 * 该标记经本次快照后应以什么形态留下：已扣完（等待的 task_id 全部落库）返回 null
 * 表示让位，否则返回扣除后的 key（无变化时原样返回，避免无谓的 Set 抖动）。
 *
 * 在途标记（taskIds 为空）原样留下、永不让位——请求往返期间没有任何依据可以判定
 * 资源已空闲。一次提交建多条任务行时（宫格按分组逐条入队），要**全部**落库才让位：
 * 只等到其中一条就交还，会在「首条已终态、其余尚未进入快照」的窗口里把资源误判为
 * 空闲。扣除逐轮累计，不要求所有 task_id 在同一个快照里同时出现——轮询每次只取最新
 * 200 行，而单次入队产生的任务行数没有上限。
 */
function keepMark(key: string, landed: ReadonlySet<string>): string | null {
  const taskIds = markTaskIds(key);
  if (taskIds.length === 0) return key; // 在途标记
  const remaining = taskIds.filter((id) => !landed.has(id));
  if (remaining.length === 0) return null;
  return remaining.length === taskIds.length ? key : withTaskIds(key, remaining);
}

// 按当前 tasks 修剪乐观占用标记（不新增标记，仅扣除已落库的 task_id 并清理已让位者）。
// 除兑现时机的顺带清理外，也要在每次 setTasks（轮询写回）时执行——真实任务行是经轮询
// 进入 store 的，标记只能在那一刻发现自己的行已落库。
function pruneSupersededOptimistic(tasks: TaskItem[], marks: ReadonlySet<string>): Set<string> {
  const landed = landedTaskIds(tasks);
  const next = new Set<string>();
  for (const key of marks) {
    const kept = keepMark(key, landed);
    if (kept !== null) next.add(kept);
  }
  return next;
}

export const useTasksStore = create<TasksState>((set, get) => {
  /**
   * begin/settle/rollback 三态的公共骨架：两种粒度只在 key 编码与落到哪个 Set 上不同。
   * `field` 选定 Set，`keyOf(taskIds)` 按已兑现的 task_id 编码出 key（空数组即在途 key）。
   */
  function beginMark(
    field: "optimisticActive" | "optimisticActiveScriptFile",
    keyOf: (taskIds: readonly string[]) => string,
  ): OptimisticHandle {
    const pendingKey = keyOf([]);
    set((s) => {
      // 顺带清理已让位的旧标记，避免 Set 在会话周期内无界增长。
      const next = pruneSupersededOptimistic(s.tasks, s[field]);
      next.add(pendingKey);
      return { [field]: next };
    });

    const drop = (replacement: string | null): void => {
      if (!get()[field].has(pendingKey)) return; // 已被回滚或被 store 重置
      set((s) => {
        const next = new Set(s[field]);
        next.delete(pendingKey);
        if (replacement !== null) {
          // 去重命中既有任务时该行往往已在 store 里，此刻即扣除，不必等下一轮轮询；
          // 全部扣完即当场让位。
          const kept = keepMark(replacement, landedTaskIds(s.tasks));
          if (kept !== null) next.add(kept);
        }
        return { [field]: next };
      });
    };

    return {
      settle: (taskIds) => drop(taskIds.length > 0 ? keyOf(taskIds) : null),
      rollback: () => drop(null),
    };
  }

  return {
    tasks: [],
    stats: defaultTaskStats,
    connected: false,
    optimisticActive: new Set(),
    optimisticActiveScriptFile: new Set(),

    setTasks: (tasks) =>
      set((s) => ({
        tasks,
        optimisticActive: pruneSupersededOptimistic(tasks, s.optimisticActive),
        optimisticActiveScriptFile: pruneSupersededOptimistic(tasks, s.optimisticActiveScriptFile),
      })),
    setStats: (stats) => set({ stats }),
    setConnected: (connected) => set({ connected }),
    beginOptimisticActive: (projectName, resourceKind, resourceId, pendingTaskType) => {
      const seq = ++optimisticSeq;
      return beginMark("optimisticActive", (taskIds) =>
        optimisticKey(projectName, resourceKind, resourceId, pendingTaskType, seq, taskIds),
      );
    },
    beginOptimisticActiveForScriptFile: (projectName, taskType, scriptFile) => {
      const seq = ++optimisticSeq;
      return beginMark("optimisticActiveScriptFile", (taskIds) =>
        optimisticScriptFileKey(projectName, taskType, scriptFile, seq, taskIds),
      );
    },
  };
});

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
 * 乐观占用：从入队请求发出到 `useTasksSSE` 下一次轮询把新任务行写进 store 之间，
 * 该 resource 在 store 里还没有对应任务行、判定为空闲。这个空窗跨越请求往返与一个
 * 轮询间隔（~3s），期间同资源的其它入口会误判可提交——image_edit 与其目标资源共用
 * 占用槽，是第一个会在此空窗内与「本资源另一 task_type」并发提交的场景（同 task_type
 * 的并发提交已被后端 dedupe 索引拦下，见 `idx_tasks_dedupe_active`，但该索引以
 * task_type 为键的一部分，不拦跨 task_type 并发）。
 *
 * `optimisticActive` 由入队动作层在**请求发出前**用 {@link OptimisticHandle} 标记、
 * 请求失败时回滚，故整个空窗都被覆盖；此处按 (projectName, resourceKind) 过滤后并入
 * 占用集，直到标记所等待的真实任务行出现为止（比对不看状态，出现即让位给真实数据）。
 * 标记按 task_id 认领自己的真实行，判据与残留风险见文件上方「乐观占用标记的编码与
 * 让位判定」。
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
  // 修剪发生在 setTasks 时，selector 可能先于它看到新快照，故这里按同一判据现算。
  const landed = landedTaskIds(tasks);
  for (const key of optimisticActive) {
    const [kProject, kResourceKind, kResourceId] = key.split("\0");
    if (kProject !== projectName || kResourceKind !== taskType) continue;
    if (keepMark(key, landed) !== null) ids.add(kResourceId);
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
 * 乐观占用：宫格入队请求发出到下一次轮询把新 grid 任务行写进 store 之间是空窗，期间
 * 本集在 store 里尚无对应 grid 任务行，分镜编辑入口会误判为空闲、与随后的切割阶段
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

  const landed = landedTaskIds(tasks);
  for (const key of optimisticActiveScriptFile) {
    const [kProject, kTaskType, kScriptFile] = key.split("\0");
    if (kProject !== projectName || kTaskType !== taskType || kScriptFile !== normalized) continue;
    if (keepMark(key, landed) !== null) return true;
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
