import { beforeEach, describe, it, expect, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import {
  defaultTaskStats,
  isActiveStatus,
  isOccupyingStatus,
  isResourceBusy,
  isScriptFileBusy,
  selectActiveResourceIds,
  selectHasActiveTaskForScriptFile,
  selectLatestTaskByResource,
  selectNeedsFastPolling,
  taskResourceKind,
  useTaskRowsByIds,
  useTasksStore,
} from "./tasks-store";
import type { TaskItem, TaskStatus } from "@/types";

function task(overrides: Partial<TaskItem> & { task_id: string }): TaskItem {
  return {
    project_name: "proj",
    task_type: "reference_video",
    media_type: "video",
    resource_id: "unit-1",
    resource_type: null,
    script_file: null,
    payload: {},
    status: "queued",
    result: null,
    error_message: null,
    cancelled_by: null,
    provider_id: null,
    provider_job_id: null,
    source: "webui",
    queued_at: "2026-07-16T00:00:00Z",
    started_at: null,
    finished_at: null,
    updated_at: "2026-07-16T00:00:00Z",
    ...overrides,
  };
}

// 乐观标记 key 的构造（与 tasks-store 内部编码一致）。seq 只用于区分同资源上的并发
// 标记，单条标记的测试取固定值即可。
const TASK_ID_SEP = "\u0001";

/** 在途标记：请求已发出、后端 task_id 未知。 */
function pendingKey(
  resourceKind: string,
  resourceId: string,
  pendingTaskType: string,
  opts: { projectName?: string; seq?: number } = {},
): string {
  const { projectName = "proj", seq = 1 } = opts;
  return `${projectName}\0${resourceKind}\0${resourceId}\0${pendingTaskType}\0${seq}\0`;
}

/** 已兑现标记：等待这些 task_id 的真实行落库。 */
function settledKey(
  resourceKind: string,
  resourceId: string,
  pendingTaskType: string,
  taskIds: string[],
  opts: { projectName?: string; seq?: number } = {},
): string {
  return pendingKey(resourceKind, resourceId, pendingTaskType, opts) + taskIds.join(TASK_ID_SEP);
}

function pendingScriptFileKey(
  taskType: string,
  scriptFile: string,
  opts: { projectName?: string; seq?: number } = {},
): string {
  const { projectName = "proj", seq = 1 } = opts;
  return `${projectName}\0${taskType}\0${scriptFile}\0${seq}\0`;
}

function settledScriptFileKey(
  taskType: string,
  scriptFile: string,
  taskIds: string[],
  opts: { projectName?: string; seq?: number } = {},
): string {
  return pendingScriptFileKey(taskType, scriptFile, opts) + taskIds.join(TASK_ID_SEP);
}

describe("isActiveStatus", () => {
  it("counts queued and running as active", () => {
    expect(isActiveStatus("queued")).toBe(true);
    expect(isActiveStatus("running")).toBe(true);
  });

  it("counts every other status as inactive", () => {
    const inactive: TaskStatus[] = ["cancelling", "succeeded", "failed", "cancelled"];
    for (const status of inactive) expect(isActiveStatus(status)).toBe(false);
  });
});

describe("isOccupyingStatus", () => {
  it("counts queued/running/cancelling as occupying", () => {
    // 占用谓词与后端 ACTIVE_TASK_STATUSES 对齐：cancelling 期间 worker 仍可能写资源，
    // 且后端 dedupe 索引会把重复提交去重到既有任务上
    expect(isOccupyingStatus("queued")).toBe(true);
    expect(isOccupyingStatus("running")).toBe(true);
    expect(isOccupyingStatus("cancelling")).toBe(true);
  });

  it("counts terminal statuses as not occupying", () => {
    const free: TaskStatus[] = ["succeeded", "failed", "cancelled"];
    for (const status of free) expect(isOccupyingStatus(status)).toBe(false);
  });
});

describe("taskResourceKind", () => {
  it("returns task_type for non-edit tasks", () => {
    expect(taskResourceKind(task({ task_id: "a", task_type: "storyboard" }))).toBe("storyboard");
    expect(taskResourceKind(task({ task_id: "b", task_type: "character" }))).toBe("character");
  });

  it("returns resource_type for image_edit tasks so edits land in the target resource slot", () => {
    expect(
      taskResourceKind(task({ task_id: "a", task_type: "image_edit", resource_type: "character" })),
    ).toBe("character");
    expect(
      taskResourceKind(task({ task_id: "b", task_type: "image_edit", resource_type: "storyboard" })),
    ).toBe("storyboard");
  });

  it("falls back to empty string when an image_edit task has no resource_type", () => {
    expect(taskResourceKind(task({ task_id: "a", task_type: "image_edit", resource_type: null }))).toBe("");
  });
});

describe("selectActiveResourceIds with image_edit", () => {
  it("counts an in-flight edit toward its resource kind's occupancy set", () => {
    // 角色 A 有一条运行中的编辑任务：应落入 character 占用集，与生成任务同槽互斥
    const tasks = [
      task({
        task_id: "edit-A",
        task_type: "image_edit",
        media_type: "image",
        resource_id: "A",
        resource_type: "character",
        status: "running",
      }),
      task({
        task_id: "gen-B",
        task_type: "character",
        media_type: "image",
        resource_id: "B",
        status: "queued",
      }),
    ];
    expect([...selectActiveResourceIds(tasks, "character", "proj")].sort()).toEqual(["A", "B"]);
    // 分镜编辑不串到 character 槽
    const sbEdit = [
      task({
        task_id: "edit-S",
        task_type: "image_edit",
        resource_id: "S",
        resource_type: "storyboard",
        status: "running",
      }),
    ];
    expect(selectActiveResourceIds(sbEdit, "character", "proj").has("S")).toBe(false);
    expect(selectActiveResourceIds(sbEdit, "storyboard", "proj").has("S")).toBe(true);
  });

  it("does not let a newer terminal edit hide a still-running generation task for the same resource", () => {
    // 生成任务 running（较旧 updated_at）+ 编辑任务 failed（较新 updated_at）：
    // 二者 task_type 不同，各自取最新行后按「任一活跃」判定，生成任务仍应算占用中
    const tasks = [
      task({
        task_id: "gen-A",
        task_type: "character",
        resource_id: "A",
        status: "running",
        updated_at: "2026-07-16T00:00:00Z",
      }),
      task({
        task_id: "edit-A",
        task_type: "image_edit",
        resource_type: "character",
        resource_id: "A",
        status: "failed",
        updated_at: "2026-07-16T01:00:00Z",
      }),
    ];
    expect(selectActiveResourceIds(tasks, "character", "proj").has("A")).toBe(true);
  });

  it("does not let a newer terminal generation hide a still-running edit task for the same resource", () => {
    // 反向对称场景：编辑任务 running（较旧）+ 生成任务 succeeded（较新），编辑仍占用中
    const tasks = [
      task({
        task_id: "edit-A",
        task_type: "image_edit",
        resource_type: "character",
        resource_id: "A",
        status: "running",
        updated_at: "2026-07-16T00:00:00Z",
      }),
      task({
        task_id: "gen-A",
        task_type: "character",
        resource_id: "A",
        status: "succeeded",
        updated_at: "2026-07-16T01:00:00Z",
      }),
    ];
    expect(selectActiveResourceIds(tasks, "character", "proj").has("A")).toBe(true);
  });
});

describe("selectActiveResourceIds optimistic occupancy", () => {
  it("counts a resource active via an in-flight marker when no real task row exists yet", () => {
    // 请求已发出、后端 task_id 未知的往返窗口：仅凭在途标记也应判定占用
    const key = pendingKey("character", "A", "image_edit");
    expect(selectActiveResourceIds([], "character", "proj", new Set([key])).has("A")).toBe(true);
  });

  it("lets the marker's own task row supersede it regardless of that row's status", () => {
    // 本次提交的任务行一旦出现（哪怕已是终态），占用判定就交回真实数据
    const key = settledKey("character", "A", "image_edit", ["edit-A"]);
    const tasks = [
      task({
        task_id: "edit-A",
        task_type: "image_edit",
        resource_type: "character",
        resource_id: "A",
        status: "succeeded",
      }),
    ];
    expect(selectActiveResourceIds(tasks, "character", "proj", new Set([key])).has("A")).toBe(false);
  });

  it("does not let another task row of the same resource supersede the marker", () => {
    // 同一资源被反复编辑：上一次编辑遗留的终态行不是本次标记等待的行，
    // 否则二次编辑期间会误判空闲
    const key = settledKey("character", "A", "image_edit", ["edit-A-second"]);
    const tasks = [
      task({
        task_id: "edit-A-first",
        task_type: "image_edit",
        resource_type: "character",
        resource_id: "A",
        status: "succeeded",
        updated_at: "2026-07-16T00:00:00Z",
      }),
    ];
    expect(selectActiveResourceIds(tasks, "character", "proj", new Set([key])).has("A")).toBe(true);
  });

  it("keeps an in-flight marker alive no matter what rows the poll brings back", () => {
    // 晚到的旧轮询快照里不会有本次提交的任务行；在途标记更不该被它清掉，
    // 否则请求往返窗口内资源会被误判为空闲、可重复入队
    const key = pendingKey("character", "A", "image_edit");
    const stale = [
      task({
        task_id: "edit-A-first",
        task_type: "image_edit",
        resource_type: "character",
        resource_id: "A",
        status: "failed",
        updated_at: "2999-01-01T00:00:00Z",
      }),
    ];
    expect(selectActiveResourceIds(stale, "character", "proj", new Set([key])).has("A")).toBe(true);
  });

  it("ignores an optimistic marker scoped to a different project or resource kind", () => {
    const key = pendingKey("character", "A", "image_edit");
    expect(selectActiveResourceIds([], "storyboard", "proj", new Set([key])).has("A")).toBe(false);
    expect(selectActiveResourceIds([], "character", "other-proj", new Set([key])).has("A")).toBe(false);
  });

  it("does not let a same-resource_id task of a different resource kind supersede the marker", () => {
    // character "A" 与 scene "A" 偶然同名(resource_id 相同)：scene 的真实行不该
    // 让 character 的乐观标记失效
    const key = settledKey("character", "A", "image_edit", ["edit-char-A"]);
    const tasks = [
      task({
        task_id: "edit-scene-A",
        task_type: "image_edit",
        resource_type: "scene",
        resource_id: "A",
        status: "succeeded",
      }),
    ];
    expect(selectActiveResourceIds(tasks, "character", "proj", new Set([key])).has("A")).toBe(true);
  });
});

describe("useTasksStore.beginOptimisticActive", () => {
  function activeIds(): Set<string> {
    const { tasks, optimisticActive } = useTasksStore.getState();
    return selectActiveResourceIds(tasks, "character", "proj", optimisticActive);
  }

  it("occupies the resource from the moment the mark is taken, before any task id is known", () => {
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });

    useTasksStore.getState().beginOptimisticActive("proj", "character", "A", "image_edit");

    expect(activeIds().has("A")).toBe(true);
  });

  it("releases the resource on rollback so a failed request leaves no residue", () => {
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });

    const mark = useTasksStore.getState().beginOptimisticActive("proj", "character", "A", "image_edit");
    mark.rollback();

    expect([...useTasksStore.getState().optimisticActive]).toEqual([]);
    expect(activeIds().has("A")).toBe(false);
  });

  it("treats settling with no task ids as a rollback", () => {
    // 后端没建任何任务行（如宫格按 scene_ids 过滤后无匹配分组）：标记若留下就永远
    // 等不到真实行，资源被误判为永久占用
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });

    const mark = useTasksStore.getState().beginOptimisticActive("proj", "character", "A", "image_edit");
    mark.settle([]);

    expect([...useTasksStore.getState().optimisticActive]).toEqual([]);
  });

  it("keeps occupying until the settled task row lands", () => {
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });

    const mark = useTasksStore.getState().beginOptimisticActive("proj", "character", "A", "image_edit");
    mark.settle(["edit-A"]);
    expect(activeIds().has("A")).toBe(true);

    useTasksStore.getState().setTasks([
      task({
        task_id: "edit-A",
        task_type: "image_edit",
        resource_type: "character",
        resource_id: "A",
        status: "succeeded",
      }),
    ]);
    expect([...useTasksStore.getState().optimisticActive]).toEqual([]);
    expect(activeIds().has("A")).toBe(false);
  });

  it("releases immediately when the settled task row is already a terminal row in the store", () => {
    // 去重命中既有任务：后端返回的是那条既有行的 task_id，它已在 store 里且已终态。
    // 标记必须当场让位——按时间戳基线比较时这条行永远「不比基线新」，标记会永久残留、
    // 把资源锁死到页面刷新为止。
    useTasksStore.setState({
      tasks: [
        task({
          task_id: "edit-A",
          task_type: "image_edit",
          resource_type: "character",
          resource_id: "A",
          status: "succeeded",
          updated_at: "2026-07-16T00:00:00Z",
        }),
      ],
      optimisticActive: new Set(),
    });

    const mark = useTasksStore.getState().beginOptimisticActive("proj", "character", "A", "image_edit");
    mark.settle(["edit-A"]);

    expect([...useTasksStore.getState().optimisticActive]).toEqual([]);
    expect(activeIds().has("A")).toBe(false);
  });

  it("rolls back only its own mark when two submissions overlap on the same resource", () => {
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });

    const first = useTasksStore.getState().beginOptimisticActive("proj", "character", "A", "image_edit");
    useTasksStore.getState().beginOptimisticActive("proj", "character", "A", "image_edit");
    first.rollback();

    expect(useTasksStore.getState().optimisticActive.size).toBe(1);
    expect(activeIds().has("A")).toBe(true);
  });

  it("prunes markers already released by their task row when taking a new mark", () => {
    useTasksStore.setState({
      tasks: [
        task({
          task_id: "edit-A",
          task_type: "image_edit",
          resource_type: "character",
          resource_id: "A",
          status: "succeeded",
        }),
      ],
      optimisticActive: new Set([settledKey("character", "A", "image_edit", ["edit-A"])]),
    });

    useTasksStore.getState().beginOptimisticActive("proj", "character", "B", "image_edit");

    const keys = [...useTasksStore.getState().optimisticActive];
    expect(keys).toHaveLength(1);
    expect(keys[0].startsWith("proj\0character\0B\0image_edit\0")).toBe(true);
  });
});

describe("useTasksStore.setTasks prunes stale optimistic markers", () => {
  it("removes a marker released by its task row even without a new mark call", () => {
    // store 只保留最近 200 条任务：真实行落地后若被更晚的大量新任务挤出该窗口，
    // 仅靠打标时的顺带清理不会再触发——轮询写回本身也要清理，否则这条已完结的旧
    // 标记会永久残留，把资源误判为占用中直到页面刷新。
    useTasksStore.setState({
      tasks: [],
      optimisticActive: new Set([settledKey("character", "A", "image_edit", ["edit-A"])]),
    });

    useTasksStore.getState().setTasks([
      task({
        task_id: "edit-A",
        task_type: "image_edit",
        resource_type: "character",
        resource_id: "A",
        status: "succeeded",
        updated_at: "2026-07-16T00:00:00Z",
      }),
    ]);

    expect([...useTasksStore.getState().optimisticActive]).toEqual([]);
  });

  it("keeps a marker whose task row has not landed yet", () => {
    const key = settledKey("character", "A", "image_edit", ["edit-A"]);
    useTasksStore.setState({ tasks: [], optimisticActive: new Set([key]) });

    useTasksStore.getState().setTasks([
      task({ task_id: "unrelated", resource_id: "B", task_type: "character" }),
    ]);

    expect([...useTasksStore.getState().optimisticActive]).toEqual([key]);
  });

  it("never drops an in-flight marker, even on a poll that carries newer unrelated rows", () => {
    // 晚到轮询不覆盖较新的乐观标记：请求往返期间任何写回都不足以判定资源已空闲
    const key = pendingKey("character", "A", "image_edit");
    useTasksStore.setState({ tasks: [], optimisticActive: new Set([key]) });

    useTasksStore.getState().setTasks([
      task({
        task_id: "edit-A-first",
        task_type: "image_edit",
        resource_type: "character",
        resource_id: "A",
        status: "failed",
        updated_at: "2999-01-01T00:00:00Z",
      }),
    ]);

    expect([...useTasksStore.getState().optimisticActive]).toEqual([key]);
  });
});

describe("selectHasActiveTaskForScriptFile", () => {
  it("returns true when a grid task for the scriptFile is queued or running", () => {
    const tasks = [
      task({
        task_id: "grid-1",
        task_type: "grid",
        resource_id: "grid-abc",
        script_file: "episode_1.json",
        status: "running",
      }),
    ];
    expect(selectHasActiveTaskForScriptFile(tasks, "grid", "episode_1.json", "proj")).toBe(true);
  });

  it("ignores tasks for a different scriptFile, project, or task_type", () => {
    const tasks = [
      task({
        task_id: "grid-other-file",
        task_type: "grid",
        resource_id: "grid-a",
        script_file: "episode_2.json",
        status: "running",
      }),
      task({
        task_id: "grid-other-project",
        task_type: "grid",
        resource_id: "grid-b",
        script_file: "episode_1.json",
        project_name: "other-proj",
        status: "running",
      }),
      task({
        task_id: "non-grid",
        task_type: "storyboard",
        resource_id: "seg-1",
        script_file: "episode_1.json",
        status: "running",
      }),
    ];
    expect(selectHasActiveTaskForScriptFile(tasks, "grid", "episode_1.json", "proj")).toBe(false);
  });

  it("normalizes an optional scripts/ prefix before comparing, either side", () => {
    // router 入队路径可能传入带 scripts/ 前缀的 script_file，Agent/SDK 工具路径经
    // validate_script_filename 强制裸文件名；两种任务行格式都要能被两种调用方式
    // 传入的 scriptFile（带或不带前缀）匹配到，不依赖调用方预先裁剪。
    const prefixedTaskTasks = [
      task({
        task_id: "grid-prefixed",
        task_type: "grid",
        resource_id: "grid-abc",
        script_file: "scripts/episode_1.json",
        status: "running",
      }),
    ];
    expect(selectHasActiveTaskForScriptFile(prefixedTaskTasks, "grid", "episode_1.json", "proj")).toBe(
      true,
    );

    const bareTaskTasks = [
      task({
        task_id: "grid-bare",
        task_type: "grid",
        resource_id: "grid-abc",
        script_file: "episode_1.json",
        status: "running",
      }),
    ];
    expect(
      selectHasActiveTaskForScriptFile(bareTaskTasks, "grid", "scripts/episode_1.json", "proj"),
    ).toBe(true);
  });

  it("does not merge to a latest row — a terminal grid task for the scriptFile stays inactive", () => {
    const tasks = [
      task({
        task_id: "grid-done",
        task_type: "grid",
        resource_id: "grid-abc",
        script_file: "episode_1.json",
        status: "succeeded",
      }),
    ];
    expect(selectHasActiveTaskForScriptFile(tasks, "grid", "episode_1.json", "proj")).toBe(false);
  });

  describe("optimistic occupancy", () => {
    it("counts the scriptFile active via an in-flight marker when no real grid row exists yet", () => {
      // 宫格请求发出到轮询写回新 grid 任务行之间的空窗：仅凭乐观标记也应判定本集占用中
      const key = pendingScriptFileKey("grid", "episode_1.json");
      expect(
        selectHasActiveTaskForScriptFile([], "grid", "episode_1.json", "proj", new Set([key])),
      ).toBe(true);
    });

    it("lets the marker's own grid row supersede it regardless of that row's status", () => {
      const key = settledScriptFileKey("grid", "episode_1.json", ["grid-1"]);
      const tasks = [
        task({
          task_id: "grid-1",
          task_type: "grid",
          resource_id: "grid-abc",
          script_file: "episode_1.json",
          status: "succeeded",
        }),
      ];
      expect(
        selectHasActiveTaskForScriptFile(tasks, "grid", "episode_1.json", "proj", new Set([key])),
      ).toBe(false);
    });

    it("releases only after all of the marker's rows land", () => {
      // 一次宫格入队会建多条任务行；只落地一条就交回真实数据的话，「首条已终态、
      // 其余尚未进入快照」的窗口里 scriptFile 会被误判为空闲。
      const key = settledScriptFileKey("grid", "episode_1.json", ["grid-1", "grid-2"]);
      const landedOne = [
        task({
          task_id: "grid-2",
          task_type: "grid",
          resource_id: "grid-def",
          script_file: "episode_1.json",
          status: "succeeded",
        }),
      ];
      expect(
        selectHasActiveTaskForScriptFile(landedOne, "grid", "episode_1.json", "proj", new Set([key])),
      ).toBe(true); // 仅一条落地且已终态，标记继续守住剩余那条

      const landedAll = [
        ...landedOne,
        task({
          task_id: "grid-1",
          task_type: "grid",
          resource_id: "grid-abc",
          script_file: "episode_1.json",
          status: "succeeded",
        }),
      ];
      expect(
        selectHasActiveTaskForScriptFile(landedAll, "grid", "episode_1.json", "proj", new Set([key])),
      ).toBe(false); // 全部落地且均已终态，标记让位
    });

    it("does not let another submission's grid row supersede the marker", () => {
      const key = settledScriptFileKey("grid", "episode_1.json", ["grid-new"]);
      const tasks = [
        task({
          task_id: "grid-old",
          task_type: "grid",
          resource_id: "grid-abc",
          script_file: "episode_1.json",
          status: "succeeded",
          updated_at: "2026-07-16T00:00:00Z",
        }),
      ];
      expect(
        selectHasActiveTaskForScriptFile(tasks, "grid", "episode_1.json", "proj", new Set([key])),
      ).toBe(true);
    });

    it("ignores a marker scoped to a different project, task type, or scriptFile", () => {
      const key = pendingScriptFileKey("grid", "episode_1.json");
      expect(
        selectHasActiveTaskForScriptFile([], "storyboard", "episode_1.json", "proj", new Set([key])),
      ).toBe(false);
      expect(
        selectHasActiveTaskForScriptFile([], "grid", "episode_1.json", "other-proj", new Set([key])),
      ).toBe(false);
      expect(
        selectHasActiveTaskForScriptFile([], "grid", "episode_2.json", "proj", new Set([key])),
      ).toBe(false);
    });
  });
});

describe("useTasksStore.beginOptimisticActiveForScriptFile", () => {
  function scriptFileActive(scriptFile = "episode_1.json"): boolean {
    const { tasks, optimisticActiveScriptFile } = useTasksStore.getState();
    return selectHasActiveTaskForScriptFile(tasks, "grid", scriptFile, "proj", optimisticActiveScriptFile);
  }

  it("occupies the scriptFile from the moment the mark is taken", () => {
    useTasksStore.setState({ tasks: [], optimisticActiveScriptFile: new Set() });

    useTasksStore.getState().beginOptimisticActiveForScriptFile("proj", "grid", "episode_1.json");

    expect(scriptFileActive()).toBe(true);
  });

  it("normalizes a scripts/ prefix in the scriptFile before storing the key", () => {
    useTasksStore.setState({ tasks: [], optimisticActiveScriptFile: new Set() });

    useTasksStore.getState().beginOptimisticActiveForScriptFile("proj", "grid", "scripts/episode_1.json");

    // 带前缀打的标记，用裸文件名查询也要命中
    expect(scriptFileActive("episode_1.json")).toBe(true);
  });

  it("releases the scriptFile on rollback", () => {
    useTasksStore.setState({ tasks: [], optimisticActiveScriptFile: new Set() });

    const mark = useTasksStore.getState().beginOptimisticActiveForScriptFile("proj", "grid", "episode_1.json");
    mark.rollback();

    expect([...useTasksStore.getState().optimisticActiveScriptFile]).toEqual([]);
    expect(scriptFileActive()).toBe(false);
  });

  it("treats settling with no task ids as a rollback", () => {
    // 宫格按 scene_ids 过滤后无匹配分组时后端不建任务行，标记不能留下
    useTasksStore.setState({ tasks: [], optimisticActiveScriptFile: new Set() });

    const mark = useTasksStore.getState().beginOptimisticActiveForScriptFile("proj", "grid", "episode_1.json");
    mark.settle([]);

    expect([...useTasksStore.getState().optimisticActiveScriptFile]).toEqual([]);
    expect(scriptFileActive()).toBe(false);
  });

  it("prunes markers already released by their task row when taking a new mark", () => {
    useTasksStore.setState({
      tasks: [
        task({
          task_id: "grid-1",
          task_type: "grid",
          resource_id: "grid-abc",
          script_file: "episode_1.json",
          status: "succeeded",
          updated_at: "2026-07-16T00:00:00Z",
        }),
      ],
      optimisticActiveScriptFile: new Set([settledScriptFileKey("grid", "episode_1.json", ["grid-1"])]),
    });

    useTasksStore.getState().beginOptimisticActiveForScriptFile("proj", "grid", "episode_2.json");

    const keys = [...useTasksStore.getState().optimisticActiveScriptFile];
    expect(keys).toHaveLength(1);
    expect(keys[0].startsWith("proj\0grid\0episode_2.json\0")).toBe(true);
  });
});

describe("useTasksStore.setTasks prunes stale optimisticActiveScriptFile markers", () => {
  it("removes a scriptFile marker released by its task row without a new mark call", () => {
    useTasksStore.setState({
      tasks: [],
      optimisticActiveScriptFile: new Set([settledScriptFileKey("grid", "episode_1.json", ["grid-1"])]),
    });

    useTasksStore.getState().setTasks([
      task({
        task_id: "grid-1",
        task_type: "grid",
        resource_id: "grid-abc",
        script_file: "episode_1.json",
        status: "succeeded",
        updated_at: "2026-07-16T00:00:00Z",
      }),
    ]);

    expect([...useTasksStore.getState().optimisticActiveScriptFile]).toEqual([]);
  });

  it("多条任务行分两轮落地也让位，不要求同一快照里同时出现", () => {
    // 轮询每次只取最新 200 行，单次入队产生的任务行数没有上限：若要求所有 task_id
    // 在同一快照里同时出现，超出窗口的批次会永久残留、锁死分镜编辑直到刷新页面。
    const key = settledScriptFileKey("grid", "episode_1.json", ["grid-1", "grid-2"]);
    useTasksStore.setState({ tasks: [], optimisticActiveScriptFile: new Set([key]) });

    const row = (taskId: string, resourceId: string) =>
      task({
        task_id: taskId,
        task_type: "grid",
        resource_id: resourceId,
        script_file: "episode_1.json",
        status: "succeeded",
        updated_at: "2026-07-16T00:00:00Z",
      });

    // 第一轮只看得到 grid-1：扣除它，标记继续守住 grid-2
    useTasksStore.getState().setTasks([row("grid-1", "grid-abc")]);
    const afterFirst = [...useTasksStore.getState().optimisticActiveScriptFile];
    expect(afterFirst).toHaveLength(1);
    expect(
      selectHasActiveTaskForScriptFile(
        useTasksStore.getState().tasks,
        "grid",
        "episode_1.json",
        "proj",
        useTasksStore.getState().optimisticActiveScriptFile,
      ),
    ).toBe(true);

    // 第二轮 grid-1 已被挤出窗口、只剩 grid-2：扣完，标记让位
    useTasksStore.getState().setTasks([row("grid-2", "grid-def")]);
    expect([...useTasksStore.getState().optimisticActiveScriptFile]).toEqual([]);
  });

  it("一次提交建多条任务行时，只落地其中一条不让位", () => {
    // 宫格按分组逐条入队：首条已快速失败、其余尚未进入快照的窗口里，若按「任一落地」
    // 让位，scriptFile 既无乐观标记也无活跃真实行，分镜编辑会短暂解禁并与后续切割竞争。
    const key = settledScriptFileKey("grid", "episode_1.json", ["grid-1", "grid-2"]);
    useTasksStore.setState({ tasks: [], optimisticActiveScriptFile: new Set([key]) });

    useTasksStore.getState().setTasks([
      task({
        task_id: "grid-1",
        task_type: "grid",
        resource_id: "grid-abc",
        script_file: "episode_1.json",
        status: "failed",
        updated_at: "2026-07-16T00:00:00Z",
      }),
    ]);
    // 已落地的 grid-1 被扣除，标记仍在等 grid-2——占用按 selector 断言，不比对 key 字面量
    expect(
      selectHasActiveTaskForScriptFile(
        useTasksStore.getState().tasks,
        "grid",
        "episode_1.json",
        "proj",
        useTasksStore.getState().optimisticActiveScriptFile,
      ),
    ).toBe(true);

    useTasksStore.getState().setTasks([
      task({
        task_id: "grid-1",
        task_type: "grid",
        resource_id: "grid-abc",
        script_file: "episode_1.json",
        status: "failed",
        updated_at: "2026-07-16T00:00:00Z",
      }),
      task({
        task_id: "grid-2",
        task_type: "grid",
        resource_id: "grid-def",
        script_file: "episode_1.json",
        status: "running",
        updated_at: "2026-07-16T00:00:01Z",
      }),
    ]);
    expect([...useTasksStore.getState().optimisticActiveScriptFile]).toEqual([]);
  });
});

describe("selectLatestTaskByResource", () => {
  it("keeps the row with the newest updated_at per resource, ignoring array order", () => {
    // 旧失败行排在新重试行之前：store 不保证顺序，须按 updated_at 归并
    const tasks = [
      task({ task_id: "old", resource_id: "unit-1", status: "failed", updated_at: "2026-07-16T00:00:00Z" }),
      task({ task_id: "new", resource_id: "unit-1", status: "running", updated_at: "2026-07-16T01:00:00Z" }),
    ];
    const latest = selectLatestTaskByResource(tasks);
    expect(latest.get("unit-1")?.task_id).toBe("new");
    expect(latest.get("unit-1")?.status).toBe("running");
  });

  it("does not let a stale later-in-array row overwrite a newer one", () => {
    const tasks = [
      task({ task_id: "new", resource_id: "unit-1", updated_at: "2026-07-16T02:00:00Z" }),
      task({ task_id: "old", resource_id: "unit-1", updated_at: "2026-07-16T00:00:00Z" }),
    ];
    expect(selectLatestTaskByResource(tasks).get("unit-1")?.task_id).toBe("new");
  });

  it("filters by projectName and taskType", () => {
    const tasks = [
      task({ task_id: "a", resource_id: "u1", project_name: "p1", task_type: "video" }),
      task({ task_id: "b", resource_id: "u2", project_name: "p2", task_type: "video" }),
      task({ task_id: "c", resource_id: "u3", project_name: "p1", task_type: "storyboard" }),
    ];
    const latest = selectLatestTaskByResource(tasks, { projectName: "p1", taskType: "video" });
    expect([...latest.keys()]).toEqual(["u1"]);
  });

  it("groups distinct resources independently", () => {
    const tasks = [
      task({ task_id: "a", resource_id: "u1" }),
      task({ task_id: "b", resource_id: "u2" }),
    ];
    expect(selectLatestTaskByResource(tasks).size).toBe(2);
  });
});

describe("selectActiveResourceIds", () => {
  it("returns resources whose latest row is active", () => {
    const tasks = [
      task({ task_id: "a", resource_id: "u1", status: "running" }),
      task({ task_id: "b", resource_id: "u2", status: "queued" }),
      task({ task_id: "c", resource_id: "u3", status: "succeeded" }),
    ];
    const ids = selectActiveResourceIds(tasks, "reference_video", "proj");
    expect([...ids].sort()).toEqual(["u1", "u2"]);
  });

  it("does not report a resource active when its newest row is a terminal retry outcome", () => {
    // 旧 running + 新 failed：最新行胜出 → 不活跃（朴素 some 会误判活跃）
    const tasks = [
      task({ task_id: "old", resource_id: "u1", status: "running", updated_at: "2026-07-16T00:00:00Z" }),
      task({ task_id: "new", resource_id: "u1", status: "failed", updated_at: "2026-07-16T01:00:00Z" }),
    ];
    expect(selectActiveResourceIds(tasks, "reference_video", "proj").has("u1")).toBe(false);
  });

  it("reports a retry active when its newest row is running despite an older failed row", () => {
    // 旧 failed + 新 running：重试不被旧失败行遮挡
    const tasks = [
      task({ task_id: "old", resource_id: "u1", status: "failed", updated_at: "2026-07-16T00:00:00Z" }),
      task({ task_id: "new", resource_id: "u1", status: "running", updated_at: "2026-07-16T01:00:00Z" }),
    ];
    expect(selectActiveResourceIds(tasks, "reference_video", "proj").has("u1")).toBe(true);
  });

  it("scopes to the given taskType and projectName", () => {
    const tasks = [
      task({ task_id: "a", resource_id: "u1", status: "running", task_type: "video", project_name: "p1" }),
      task({ task_id: "b", resource_id: "u2", status: "running", task_type: "storyboard", project_name: "p1" }),
      task({ task_id: "c", resource_id: "u3", status: "running", task_type: "video", project_name: "p2" }),
    ];
    expect([...selectActiveResourceIds(tasks, "video", "p1")]).toEqual(["u1"]);
  });

  it("keeps a cancelling task in the occupancy set", () => {
    // 取消窗口期资源仍被占用：按钮须保持禁用，否则重提交会撞后端 dedupe 索引
    // 返回既有任务、造成「提交成功却没有新任务」的谎报
    const tasks = [task({ task_id: "a", resource_id: "u1", status: "cancelling" })];
    expect(selectActiveResourceIds(tasks, "reference_video", "proj").has("u1")).toBe(true);
  });
});

describe("isResourceBusy", () => {
  beforeEach(() => {
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
  });

  it("reads the store snapshot at call time rather than a captured one", () => {
    // 该函数存在的全部理由：调用方在提交那一刻拿到的必须是最新占用态，
    // 而非渲染期（或上一次调用时）捕获的快照。
    expect(isResourceBusy("reference_video", "proj", "u1")).toBe(false);
    useTasksStore.setState({ tasks: [task({ task_id: "a", resource_id: "u1", status: "running" })] });
    expect(isResourceBusy("reference_video", "proj", "u1")).toBe(true);
  });

  it("counts the optimistic in-flight markers held in the store", () => {
    // 入队动作层在请求发出前打的标记也要被看到，否则同 tick 内的连点会漏过
    useTasksStore.setState({ optimisticActive: new Set([pendingKey("character", "A", "image_edit")]) });
    expect(isResourceBusy("character", "proj", "A")).toBe(true);
  });

  it("scopes to the given kind and projectName", () => {
    useTasksStore.setState({
      tasks: [task({ task_id: "a", resource_id: "u1", status: "running", task_type: "video", project_name: "p1" })],
    });
    expect(isResourceBusy("video", "p1", "u1")).toBe(true);
    expect(isResourceBusy("storyboard", "p1", "u1")).toBe(false);
    expect(isResourceBusy("video", "p2", "u1")).toBe(false);
  });
});

describe("isScriptFileBusy", () => {
  beforeEach(() => {
    useTasksStore.setState({ tasks: [], optimisticActiveScriptFile: new Set() });
  });

  it("reads the store snapshot at call time", () => {
    // 与 isResourceBusy 同理：提交那一刻必须拿最新占用态，而非渲染期捕获的快照。
    expect(isScriptFileBusy("grid", "episode_1.json", "proj")).toBe(false);
    useTasksStore.setState({
      tasks: [
        task({ task_id: "g1", task_type: "grid", script_file: "episode_1.json", status: "running" }),
      ],
    });
    expect(isScriptFileBusy("grid", "episode_1.json", "proj")).toBe(true);
  });

  it("normalizes the scripts/ prefix on both sides", () => {
    // episode 元数据带 scripts/ 前缀、任务行不一定带，两边都要归一后再比。
    useTasksStore.setState({
      tasks: [
        task({ task_id: "g1", task_type: "grid", script_file: "episode_1.json", status: "running" }),
      ],
    });
    expect(isScriptFileBusy("grid", "scripts/episode_1.json", "proj")).toBe(true);
  });

  it("returns false when scriptFile or projectName is missing", () => {
    useTasksStore.setState({
      tasks: [
        task({ task_id: "g1", task_type: "grid", script_file: "episode_1.json", status: "running" }),
      ],
    });
    expect(isScriptFileBusy("grid", undefined, "proj")).toBe(false);
    expect(isScriptFileBusy("grid", "episode_1.json", null)).toBe(false);
  });
});

describe("selectHasActiveTaskForScriptFile with cancelling", () => {
  it("counts a cancelling grid task as occupying the scriptFile", () => {
    const tasks = [
      task({
        task_id: "grid-1",
        task_type: "grid",
        resource_id: "grid-abc",
        script_file: "episode_1.json",
        status: "cancelling",
      }),
    ];
    expect(selectHasActiveTaskForScriptFile(tasks, "grid", "episode_1.json", "proj")).toBe(true);
  });
});

describe("refreshTasks（多入口共享刷新的在途合并）", () => {
  beforeEach(() => {
    useTasksStore.setState(useTasksStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  function mockFetch(items: TaskItem[] = []) {
    const listSpy = vi.spyOn(API, "listTasks").mockResolvedValue({
      items,
      total: items.length,
      page: 1,
      page_size: 200,
    });
    vi.spyOn(API, "getTaskStats").mockResolvedValue({
      stats: {
        queued: 0,
        running: 0,
        cancelling: 0,
        succeeded: 0,
        failed: 0,
        cancelled: 0,
        total: 0,
      },
    });
    return listSpy;
  }

  it("作用域未启用时不发请求", async () => {
    const listSpy = mockFetch();

    await useTasksStore.getState().refreshTasks();

    expect(listSpy).not.toHaveBeenCalled();
  });

  it("按作用域拉取并写回 store", async () => {
    const listSpy = mockFetch([task({ task_id: "t1" })]);
    useTasksStore.getState().setRefreshScope({ projectName: "proj" });

    await useTasksStore.getState().refreshTasks();

    expect(listSpy).toHaveBeenCalledWith({ projectName: "proj", pageSize: 200 });
    expect(useTasksStore.getState().tasks).toHaveLength(1);
    expect(useTasksStore.getState().connected).toBe(true);
  });

  it("projectName 为 null 时拉全局任务（不按项目过滤）", async () => {
    const listSpy = mockFetch();
    useTasksStore.getState().setRefreshScope({ projectName: null });

    await useTasksStore.getState().refreshTasks();

    expect(listSpy).toHaveBeenCalledWith({ projectName: undefined, pageSize: 200 });
  });

  it("轮询看到参考生视频任务转成功时失效单元缓存，且不重复失效", async () => {
    // 成片重拉此前只挂在项目事件 SSE 的终态分支上：SSE 断线或丢掉这条事件时，任务状态还能
    // 靠轮询兜底恢复，成片却一直不出现。轮询走同一次失效补上这个缺口。
    useAppStore.setState({ referenceVideoUnitsRevision: 0 });
    useTasksStore.getState().setRefreshScope({ projectName: "proj" });

    mockFetch([task({ task_id: "rv1", task_type: "reference_video", status: "running" })]);
    await useTasksStore.getState().refreshTasks();
    expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(0);

    vi.restoreAllMocks();
    mockFetch([task({ task_id: "rv1", task_type: "reference_video", status: "succeeded" })]);
    await useTasksStore.getState().refreshTasks();
    expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(1);

    // 同一条任务在后续轮次里已是终态，不再重复失效。
    await useTasksStore.getState().refreshTasks();
    expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(1);
  });

  it("轮询看到参考单元旁白配音任务转成功时失效单元缓存", async () => {
    // SSE 是完成事件主通道；它静默失速时，任务轮询也必须让新写入剧本的旁白路径出现在
    // reference-video-store，否则播放器会一直持有生成前的旧 unit 快照。
    useAppStore.setState({ referenceVideoUnitsRevision: 0 });
    useTasksStore.getState().setRefreshScope({ projectName: "proj" });

    mockFetch([task({ task_id: "tts1", task_type: "tts", media_type: "audio", status: "running" })]);
    await useTasksStore.getState().refreshTasks();
    expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(0);

    vi.restoreAllMocks();
    mockFetch([task({ task_id: "tts1", task_type: "tts", media_type: "audio", status: "succeeded" })]);
    await useTasksStore.getState().refreshTasks();
    expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(1);
  });

  it("整个生命周期落在两次轮询之间的参考生视频任务也算完成", async () => {
    // 空闲档间隔较长，provider 命中缓存时任务可能在一个间隔内走完：它是首次以 succeeded
    // 出现的，若要求上一轮见过就会漏掉，画布仍看不到成片。
    useAppStore.setState({ referenceVideoUnitsRevision: 0 });
    useTasksStore.getState().setRefreshScope({ projectName: "proj" });

    mockFetch([task({ task_id: "other", task_type: "storyboard", status: "succeeded" })]);
    await useTasksStore.getState().refreshTasks();
    expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(0);

    vi.restoreAllMocks();
    mockFetch([
      task({ task_id: "other", task_type: "storyboard", status: "succeeded" }),
      task({ task_id: "rv-fast", task_type: "reference_video", status: "succeeded" }),
    ]);
    await useTasksStore.getState().refreshTasks();
    expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(1);
  });

  it("切作用域后的第一轮只建基线，不把新作用域的历史成功任务当作刚完成", async () => {
    useAppStore.setState({ referenceVideoUnitsRevision: 0 });
    useTasksStore.getState().setRefreshScope({ projectName: "proj" });
    mockFetch([]);
    await useTasksStore.getState().refreshTasks();

    vi.restoreAllMocks();
    useTasksStore.getState().setRefreshScope({ projectName: "other-proj" });
    mockFetch([task({ task_id: "rv-old", task_type: "reference_video", status: "succeeded" })]);
    await useTasksStore.getState().refreshTasks();

    expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(0);
  });

  it("首轮拉取不把历史成功的参考生视频任务当作刚完成", async () => {
    useAppStore.setState({ referenceVideoUnitsRevision: 0 });
    useTasksStore.getState().setRefreshScope({ projectName: "proj" });
    mockFetch([task({ task_id: "rv-old", task_type: "reference_video", status: "succeeded" })]);

    await useTasksStore.getState().refreshTasks();

    expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(0);
  });

  it("在途期间到达的多次调用合并为结束后再跑一轮", async () => {
    // 两个入口（轮询 + SSE 任务终态）同时刷新时不各自发请求：首轮 1 次 + 合并轮 1 次。
    const releases: Array<() => void> = [];
    const listSpy = vi.spyOn(API, "listTasks").mockImplementation(
      () =>
        new Promise((resolve) => {
          releases.push(() => resolve({ items: [], total: 0, page: 1, page_size: 200 }));
        }),
    );
    vi.spyOn(API, "getTaskStats").mockResolvedValue({
      stats: {
        queued: 0,
        running: 0,
        cancelling: 0,
        succeeded: 0,
        failed: 0,
        cancelled: 0,
        total: 0,
      },
    });
    useTasksStore.getState().setRefreshScope({ projectName: "proj" });

    const first = useTasksStore.getState().refreshTasks();
    const second = useTasksStore.getState().refreshTasks();
    const third = useTasksStore.getState().refreshTasks();

    expect(listSpy).toHaveBeenCalledTimes(1);

    releases[0]();
    // 首轮落定后合并轮才发第二次请求——两个排队调用方合并成这一轮，不是各发一次。
    await vi.waitFor(() => expect(releases).toHaveLength(2));
    releases[1]();
    await Promise.all([first, second, third]);

    expect(listSpy).toHaveBeenCalledTimes(2);
  });

  it("落定时作用域已切换的迟到响应不写回", async () => {
    const releases: Array<(items: TaskItem[]) => void> = [];
    vi.spyOn(API, "listTasks").mockImplementation(
      () =>
        new Promise((resolve) => {
          releases.push((items) => resolve({ items, total: items.length, page: 1, page_size: 200 }));
        }),
    );
    vi.spyOn(API, "getTaskStats").mockResolvedValue({
      stats: {
        queued: 0,
        running: 0,
        cancelling: 0,
        succeeded: 0,
        failed: 0,
        cancelled: 0,
        total: 0,
      },
    });
    useTasksStore.getState().setRefreshScope({ projectName: "old-project" });

    const pending = useTasksStore.getState().refreshTasks();
    await Promise.resolve();
    // 切项目：旧项目的在途响应此刻才回来，不能盖住接管方的数据。
    useTasksStore.getState().setRefreshScope({ projectName: "new-project" });
    releases[0]([task({ task_id: "stale" })]);
    await pending;

    expect(useTasksStore.getState().tasks).toEqual([]);
  });

  it("请求失败时置 connected=false 并留旧数据", async () => {
    mockFetch([task({ task_id: "t1" })]);
    useTasksStore.getState().setRefreshScope({ projectName: "proj" });
    await useTasksStore.getState().refreshTasks();

    vi.spyOn(API, "listTasks").mockRejectedValue(new Error("network"));
    await useTasksStore.getState().refreshTasks();

    expect(useTasksStore.getState().connected).toBe(false);
    expect(useTasksStore.getState().tasks).toHaveLength(1);
  });
});

describe("selectNeedsFastPolling", () => {
  const idle = {
    tasks: [] as TaskItem[],
    stats: defaultTaskStats,
    connected: true,
    refreshScope: { projectName: "proj" },
    optimisticActive: new Set<string>(),
    optimisticActiveScriptFile: new Set<string>(),
  };

  it("空闲且连接正常时退到低频档", () => {
    expect(selectNeedsFastPolling(idle)).toBe(false);
  });

  it("有任务未落终态时留在高频档", () => {
    expect(
      selectNeedsFastPolling({ ...idle, stats: { ...defaultTaskStats, cancelling: 1 } }),
    ).toBe(true);
  });

  it("上一轮拉取失败时留在高频档", () => {
    expect(selectNeedsFastPolling({ ...idle, connected: false })).toBe(true);
  });

  it("当前作用域内的乐观标记让判据留在高频档", () => {
    expect(
      selectNeedsFastPolling({
        ...idle,
        optimisticActive: new Set(["proj\0character\0A\0image_edit\0"]),
      }),
    ).toBe(true);
    expect(
      selectNeedsFastPolling({
        ...idle,
        optimisticActiveScriptFile: new Set(["proj\0grid\0episode_1.json\0"]),
      }),
    ).toBe(true);
  });

  it("别的项目残留的乐观标记不把当前作用域钉在高频档", () => {
    // 在项目 A 打标后、真实任务行落地前切到项目 B：A 的标记再也等不到同项目任务行来修剪，
    // 若计入判据，B 即便完全空闲也会一直 3 秒轮询。
    expect(
      selectNeedsFastPolling({
        ...idle,
        refreshScope: { projectName: "other-proj" },
        optimisticActive: new Set(["proj\0character\0A\0image_edit\0"]),
        optimisticActiveScriptFile: new Set(["proj\0grid\0episode_1.json\0"]),
      }),
    ).toBe(false);
  });

  it("统计尚未反映的在途任务行也让判据留在高频档", () => {
    // 任务列表与统计是两个并发请求、不是同一快照：别处恰在本轮刷新期间入队时，统计可能
    // 读到零活跃而列表已含那条 queued 行。只看统计会把这一轮判成空闲，该任务的中间态要
    // 等满一个空闲间隔才出现在界面上。
    expect(
      selectNeedsFastPolling({
        ...idle,
        tasks: [task({ task_id: "t-new", status: "queued" })],
      }),
    ).toBe(true);
  });

  it("列表里只剩终态任务时不阻止退到低频档", () => {
    expect(
      selectNeedsFastPolling({
        ...idle,
        tasks: [
          task({ task_id: "t-done", status: "succeeded" }),
          task({ task_id: "t-fail", status: "failed" }),
        ],
      }),
    ).toBe(false);
  });

  it("不按项目过滤（全局作用域）时所有标记都计入", () => {
    expect(
      selectNeedsFastPolling({
        ...idle,
        refreshScope: { projectName: null },
        optimisticActive: new Set(["proj\0character\0A\0image_edit\0"]),
      }),
    ).toBe(true);
  });
});

describe("useTaskRowsByIds", () => {
  beforeEach(() => {
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
  });

  it("任务字段变化时向订阅方推送新读数", () => {
    const ids = ["t-1"];
    const { result } = renderHook(() => useTaskRowsByIds(ids));

    act(() => {
      useTasksStore.setState({ tasks: [task({ task_id: "t-1", status: "running" })] });
    });
    expect(result.current.get("t-1")?.status).toBe("running");

    act(() => {
      useTasksStore.setState({ tasks: [task({ task_id: "t-1", status: "succeeded" })] });
    });
    expect(result.current.get("t-1")?.status).toBe("succeeded");
  });

  it("无关任务变化时不产生新的读数对象", () => {
    const watched = task({ task_id: "t-1" });
    useTasksStore.setState({ tasks: [watched, task({ task_id: "t-2" })] });
    const ids = ["t-1"];
    const { result } = renderHook(() => useTaskRowsByIds(ids));
    const first = result.current;

    act(() => {
      useTasksStore.setState({
        tasks: [watched, task({ task_id: "t-2", status: "succeeded" })],
      });
    });
    expect(result.current).toBe(first);
  });
});
