import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type { TaskItem, TaskStats, TaskStatus } from "@/types";

/** 单次刷新拉取的任务条数上限。 */
const TASKS_PAGE_SIZE = 200;

/**
 * 占用判定关心的全量资源种类（不含 image_edit——它按 resource_type 归入其中之一）。
 *
 * 占用集的读写两侧共用这一个真相源：写侧 {@link TasksState.beginOptimisticActive} 打标、
 * 读侧 {@link selectActiveResourceIds} 过滤，两侧的 kind 拼写必须一致才能匹配上同一个槽。
 * 收紧为联合类型即为把「拼错就静默不占用」的失败模式提前到编译期。
 */
export type ResourceKind =
  | "character"
  | "scene"
  | "prop"
  | "product"
  | "storyboard"
  | "video"
  | "tts"
  | "reference_video"
  | "grid";

/** 可做指令式编辑的资源种类；`image_edit` 任务按此归入对应资源槽。 */
export type ImageEditResourceKind = Extract<
  ResourceKind,
  "character" | "scene" | "prop" | "product" | "storyboard"
>;

/**
 * 刷新作用域：`projectName === null` 表示「不按项目过滤」（拉全局任务），整个 scope 为
 * `null` 表示「未启用」——此时 {@link TasksState.refreshTasks} 不发请求。作用域由
 * `useTaskRefresh` 单点登记，其余入口（项目事件 SSE 推来的任务终态）只调 refreshTasks、
 * 不各自决定拉哪个项目，避免全局任务列表被项目过滤结果覆盖。
 */
export interface TasksRefreshScope {
  projectName: string | null;
}

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
  refreshScope: TasksRefreshScope | null;
  /** 乐观占用标记，见下方 {@link selectActiveResourceIds} 的乐观占用小节。 */
  optimisticActive: Set<string>;
  /** 乐观占用标记（scriptFile 粒度），见下方 {@link selectHasActiveTaskForScriptFile} 的乐观占用小节。 */
  optimisticActiveScriptFile: Set<string>;

  // Actions
  setTasks: (tasks: TaskItem[]) => void;
  setStats: (stats: TaskStats) => void;
  setConnected: (connected: boolean) => void;
  setRefreshScope: (scope: TasksRefreshScope | null) => void;
  /**
   * 拉取当前作用域的任务列表与统计并写回 store。
   *
   * 多入口共享（轮询定时器 + 项目事件 SSE 推来的任务终态）故收敛为单个 action，并在
   * 内部做在途合并：已有刷新在途时新请求合并为「结束后再跑一轮」，各调用方分别结算
   * 自己那一轮。否则两个入口同时刷新会交错写回，先发后到的旧结果盖住新结果。
   */
  refreshTasks: () => Promise<void>;
  beginOptimisticActive: (
    projectName: string,
    resourceKind: ResourceKind,
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
  resourceKind: ResourceKind,
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
  // 刷新的在途合并协调状态（非响应式单例，不进 store state，避免触发订阅重渲染）。
  // 与 projects-store.refreshProject 同构，随 store 一同创建，不跨 store 实例残留。
  let refreshRunning = false;
  let refreshQueued = false;
  // 排队轮调用方各自的 resolve：每个调用方只对自己实际服务的那一轮结果负责。
  let queuedResolvers: Array<() => void> = [];
  // 已建立任务基线的作用域；null 表示下一轮是该作用域的第一轮，只建基线不做终态判定。
  let baselineScopeKey: string | null = null;

  /**
   * 本轮拉取里是否有会写回 reference unit 的任务刚由非终态转成功。
   *
   * 参考生视频画布靠 `referenceVideoUnitsRevision` 重拉成片和旁白路径，而自增主通道是项目
   * 事件 SSE：SSE 断线、静默失速或丢掉这一条事件时，任务状态还能靠轮询兜底恢复，unit
   * 快照却一直不更新，直到画布重新挂载。这里让 reference_video / tts 的轮询成功也走同一次
   * 失效，补上兜底路径的缺口；非参考生视频没有挂载该画布，多一次 revision 自增不触发请求。
   *
   * 判据是「这一轮成功、上一轮不成功」，其中「上一轮没见过」也算不成功——任务的入队与完成
   * 整个落在两次轮询之间时（空闲档间隔较长，provider 命中缓存时可能秒回），它是首次以
   * succeeded 出现的，要求上一轮见过就会漏掉这一类。下一轮它在旧快照里已是 succeeded，
   * 因此不会重复失效。
   *
   * 作用域的第一轮只建基线、不判定：那一轮的旧快照要么是空的、要么属于上一个项目，把历史
   * 成功任务全当成刚完成没有意义。基线由 `baselineScopeKey` 标记，切项目或停用后重置。
   */
  const hasNewlySucceededReferenceUnitAsset = (
    prev: readonly TaskItem[],
    next: readonly TaskItem[],
  ): boolean => {
    const prevStatus = new Map(prev.map((t) => [t.task_id, t.status]));
    return next.some(
      (t) =>
        (t.task_type === "reference_video" || t.task_type === "tts") &&
        t.status === "succeeded" &&
        prevStatus.get(t.task_id) !== "succeeded",
    );
  };

  // 单轮拉取；自身不抛错，失败只把 connected 置 false 并留旧数据。
  const runTasksRefresh = async (): Promise<void> => {
    const scope = get().refreshScope;
    if (!scope) return;
    const { projectName } = scope;

    // 落定时作用域可能已被切项目/停用改写：迟到响应不写回，否则旧项目的任务列表会盖住
    // 接管方刚写入的数据（停用时则会盖住本该清空的 store）。
    const scopeUnchanged = () => {
      const current = get().refreshScope;
      return current !== null && current.projectName === projectName;
    };

    try {
      const [tasksRes, statsRes] = await Promise.all([
        API.listTasks({ projectName: projectName ?? undefined, pageSize: TASKS_PAGE_SIZE }),
        API.getTaskStats(projectName ?? null),
      ]);
      if (!scopeUnchanged()) return;
      const scopeKey = projectName ?? "";
      const unitsStale =
        baselineScopeKey === scopeKey &&
        hasNewlySucceededReferenceUnitAsset(get().tasks, tasksRes.items);
      baselineScopeKey = scopeKey;
      get().setTasks(tasksRes.items);
      get().setStats(statsRes.stats);
      get().setConnected(true);
      // 留在 try 内是有意的：失效只是一次 zustand set，没有抛出路径；而把它挪到 try 外会让
      // 本函数不再满足「自身不抛错」——项目事件 SSE 那一路是裸 void 调用 refreshTasks，
      // 抛出会变成未处理的 rejection，且在途合并里排队的调用方永远等不到结算。
      if (unitsStale) {
        useAppStore.getState().invalidateReferenceVideoUnits();
      }
    } catch {
      if (!scopeUnchanged()) return;
      get().setConnected(false);
    }
  };

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
    refreshScope: null,
    optimisticActive: new Set(),
    optimisticActiveScriptFile: new Set(),

    setRefreshScope: (scope) => {
      // 切项目与停用都会清空/替换任务快照，旧基线不再可比：下一轮重新建基线。
      baselineScopeKey = null;
      set({ refreshScope: scope });
    },
    refreshTasks: async () => {
      if (refreshRunning) {
        refreshQueued = true;
        return new Promise<void>((resolve) => {
          queuedResolvers.push(resolve);
        });
      }
      refreshRunning = true;
      try {
        await runTasksRefresh();
        while (refreshQueued) {
          refreshQueued = false;
          const resolvers = queuedResolvers;
          queuedResolvers = [];
          await runTasksRefresh();
          for (const resolve of resolvers) resolve();
        }
      } finally {
        refreshRunning = false;
      }
    },

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
// 消费点（画布 loading 派生、视频单元状态等）此前各自重写两条隐性契约：
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
 * 标记集中是否有属于当前刷新作用域的条目。两类乐观标记的 key 均以 `projectName` 打头，
 * 故统一按前缀判定；作用域为「不按项目过滤」（`projectName === null`）时全部计入。
 *
 * 按作用域筛而非直接看 `size`：标记只在同项目的真实任务行落地时才被修剪，切到别的项目
 * 后原项目的残留标记永远等不到修剪，若计入判据会把兜底轮询永久钉在高频档。
 */
function hasOptimisticInScope(
  marks: ReadonlySet<string>,
  scopeProjectName: string | null,
): boolean {
  if (marks.size === 0) return false;
  if (scopeProjectName === null) return true;
  const prefix = `${scopeProjectName}\0`;
  for (const key of marks) {
    if (key.startsWith(prefix)) return true;
  }
  return false;
}

/**
 * 兜底轮询是否需要留在高频档。三种情形都算「需要」：
 *
 * - **有任务未落终态**。以 stats 为主判据：它覆盖整个作用域，而 tasks 只是分页后的前 N 条。
 *   中间态（queued→running）没有事件推送，只有轮询能看见。tasks 一并计入是因为二者由两个
 *   并发请求分别读出、彼此不是同一快照：别处（另一客户端或 Skill）恰在本轮刷新期间入队时，
 *   stats 可能读到零活跃而 tasks 已含那条 queued 行，只看 stats 会把这一轮判成空闲，该任务
 *   的中间态要等满一个空闲间隔才可见。
 * - **当前作用域内有乐观占用标记**。入队成功到该任务出现在 stats 之间还有一段空窗，
 *   此时若判为空闲，新任务要等一整个空闲间隔才在界面上出现。
 * - **上一轮拉取失败**（`connected === false`）。此时 stats 是过期数据，据它判空闲会把
 *   恢复推迟一整个空闲间隔；失败后本就该尽快重试。
 */
export function selectNeedsFastPolling(s: {
  tasks: readonly TaskItem[];
  stats: TaskStats;
  connected: boolean;
  refreshScope: TasksRefreshScope | null;
  optimisticActive: ReadonlySet<string>;
  optimisticActiveScriptFile: ReadonlySet<string>;
}): boolean {
  // 作用域未启用时 refreshTasks 不发请求，标记落在哪个项目都无从对账，一律不计入。
  const scope = s.refreshScope;
  return (
    !s.connected ||
    s.stats.queued + s.stats.running + s.stats.cancelling > 0 ||
    s.tasks.some((t) => isOccupyingStatus(t.status)) ||
    (scope !== null &&
      (hasOptimisticInScope(s.optimisticActive, scope.projectName) ||
        hasOptimisticInScope(s.optimisticActiveScriptFile, scope.projectName)))
  );
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
 * `voice_sample`（角色 TTS 试听样本）同理归入 `character`——它与该角色的资产图生成/
 * 编辑/上传共用同一占用槽（见 {@link enqueueCharacterVoiceSample}），乐观标记在请求
 * 发出前就用 `character` kind 打标，真实任务行落库后须归到同一 kind 才能让位不断档。
 *
 * 两处断言是数据边界：{@link TaskItem} 的字段来自后端、TS 管不到其值域。后端若出现未在
 * {@link ResourceKind} 登记的种类，该行匹配不上任何占用槽（退化为不参与占用判定），
 * 与收紧前的行为一致；真正被联合类型堵住的是前端调用方自己拼错 kind。
 */
export function taskResourceKind(task: TaskItem): ResourceKind | "" {
  if (task.task_type === "image_edit") {
    return (task.resource_type as ResourceKind | null) ?? "";
  }
  if (task.task_type === "voice_sample") {
    return "character";
  }
  return task.task_type as ResourceKind;
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
 * 乐观占用：从入队请求发出到下一次刷新把新任务行写进 store 之间，该 resource 在 store
 * 里还没有对应任务行、判定为空闲。这个空窗跨越请求往返与一个轮询间隔（标记本身会把兜底
 * 轮询拉回忙碌档，见 {@link selectNeedsFastPolling}，故上限是一个忙碌档间隔而非空闲档
 * 间隔）；期间同资源的其它入口会误判可提交——image_edit 与其目标资源共用占用槽，是第一个
 * 会在此空窗内与「本资源另一 task_type」并发提交的场景（同 task_type 的并发提交已被后端
 * dedupe 索引拦下，见 `idx_tasks_dedupe_active`，但该索引以 task_type 为键的一部分，
 * 不拦跨 task_type 并发）。
 *
 * `optimisticActive` 由入队动作层在**请求发出前**用 {@link OptimisticHandle} 标记、
 * 请求失败时回滚，故整个空窗都被覆盖；此处按 (projectName, resourceKind) 过滤后并入
 * 占用集，直到标记所等待的真实任务行出现为止（比对不看状态，出现即让位给真实数据）。
 * 标记按 task_id 认领自己的真实行，判据与残留风险见文件上方「乐观占用标记的编码与
 * 让位判定」。
 */
export function selectActiveResourceIds(
  tasks: TaskItem[],
  taskType: ResourceKind,
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

/**
 * 提交时刻复核占用态的统一入口：封装 `getState()` 新鲜读 + {@link selectActiveResourceIds} +
 * key 拼装，供各写入控件在提交那一刻直接判定，不必各自重复这三步。
 */
export function isResourceBusy(kind: ResourceKind, projectName: string, resourceId: string): boolean {
  const { tasks, optimisticActive } = useTasksStore.getState();
  return selectActiveResourceIds(tasks, kind, projectName, optimisticActive).has(resourceId);
}

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

/**
 * scriptFile 粒度的提交时刻复核入口，与 {@link isResourceBusy} 同构（`getState()` 新鲜读）。
 *
 * 存在的理由与 resource 粒度那条相同——渲染时刻的 prop 反映的是上次渲染，store 更新到重渲染
 * 提交之间用户仍可能点下去。粒度换成 scriptFile 是因为 grid 任务的 resource_id 是 grid_id、
 * 归不进按分镜 resource_id 的判定（见 {@link selectHasActiveTaskForScriptFile}）。
 * scriptFile/projectName 缺失时返回 false，与 hook 版同口径。
 */
export function isScriptFileBusy(
  taskType: string,
  scriptFile: string | undefined | null,
  projectName: string | undefined | null,
): boolean {
  if (!scriptFile || !projectName) return false;
  const { tasks, optimisticActiveScriptFile } = useTasksStore.getState();
  return selectHasActiveTaskForScriptFile(
    tasks,
    taskType,
    scriptFile,
    projectName,
    optimisticActiveScriptFile,
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
  taskType: ResourceKind,
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
