import { describe, it, expect } from "vitest";
import {
  isActiveStatus,
  isOccupyingStatus,
  isTerminalStatus,
  selectActiveResourceIds,
  selectHasActiveTaskForScriptFile,
  selectLatestTaskByResource,
  taskResourceKind,
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

describe("isTerminalStatus", () => {
  it("counts succeeded/failed/cancelled as terminal", () => {
    expect(isTerminalStatus("succeeded")).toBe(true);
    expect(isTerminalStatus("failed")).toBe(true);
    expect(isTerminalStatus("cancelled")).toBe(true);
  });

  it("counts in-flight statuses as non-terminal", () => {
    const live: TaskStatus[] = ["queued", "running", "cancelling"];
    for (const status of live) expect(isTerminalStatus(status)).toBe(false);
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
  it("counts a resource active via an optimistic marker when no real task row exists yet", () => {
    // 提交成功但 SSE 尚未把任务行写进 store 的空窗：仅凭乐观标记也应判定占用（空 baseline）
    const key = "proj\0character\0A\0image_edit\0";
    expect(selectActiveResourceIds([], "character", "proj", new Set([key])).has("A")).toBe(true);
  });

  it("lets a real task row newer than the baseline supersede the optimistic marker regardless of its status", () => {
    // 真实任务行一旦出现（哪怕已是终态），不再依赖乐观标记——但此时该行本身若是活跃态，
    // 仍会通过 selectActiveResourceIds 主逻辑判定占用；这里验证的是"不因乐观标记残留而
    // 对已完结的真实行仍强制判占用"
    const key = "proj\0character\0A\0image_edit\0";
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

  it("does not let a stale task row predating the baseline supersede a marker for a repeat edit", () => {
    // 同一资源被反复编辑：本次标记的 baseline 取自上一次编辑遗留的终态行 updated_at，
    // 该旧行本身不该被当作"本次"标记等待的真实行，否则二次编辑期间会误判空闲
    const key = `proj\0character\0A\0image_edit\0${"2026-07-16T00:00:00Z"}`;
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

  it("ignores an optimistic marker scoped to a different project or resource kind", () => {
    const key = "proj\0character\0A\0image_edit\0";
    expect(selectActiveResourceIds([], "storyboard", "proj", new Set([key])).has("A")).toBe(false);
    expect(selectActiveResourceIds([], "character", "other-proj", new Set([key])).has("A")).toBe(false);
  });

  it("does not let a same-resource_id task of a different resource kind supersede the marker", () => {
    // character "A" 与 scene "A" 偶然同名(resource_id 相同)：scene 的真实行不该
    // 让 character 的乐观标记失效
    const key = "proj\0character\0A\0image_edit\0";
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

describe("useTasksStore.markOptimisticActive", () => {
  it("marks a resource optimistically active and prunes markers already superseded by a real row", () => {
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
      optimisticActive: new Set([`proj\0character\0A\0image_edit\0${"2025-01-01T00:00:00Z"}`]),
    });

    // 标记一个新资源 B 的同时，A 的旧标记（baseline 早于真实终态行）已被取代，应被顺带清理
    useTasksStore.getState().markOptimisticActive("proj", "character", "B", "image_edit");

    const keys = [...useTasksStore.getState().optimisticActive];
    expect(keys).toEqual(["proj\0character\0B\0image_edit\0"]);
  });

  it("uses the latest matching real row's updated_at as baseline so a repeat edit isn't superseded by the old row", () => {
    // A 已有一条旧终态行；对 A 发起新一轮编辑时，baseline 应取该旧行的 updated_at，
    // 使得新标记不会被这条旧行自己判定为"已超越"
    useTasksStore.setState({
      tasks: [
        task({
          task_id: "edit-A-first",
          task_type: "image_edit",
          resource_type: "character",
          resource_id: "A",
          status: "succeeded",
          updated_at: "2026-07-16T00:00:00Z",
        }),
      ],
      optimisticActive: new Set(),
    });

    useTasksStore.getState().markOptimisticActive("proj", "character", "A", "image_edit");

    const keys = [...useTasksStore.getState().optimisticActive];
    expect(keys).toEqual([`proj\0character\0A\0image_edit\0${"2026-07-16T00:00:00Z"}`]);

    const { tasks, optimisticActive } = useTasksStore.getState();
    expect(selectActiveResourceIds(tasks, "character", "proj", optimisticActive).has("A")).toBe(true);
  });
});

describe("useTasksStore.setTasks prunes stale optimistic markers", () => {
  it("removes a marker superseded by a real row even without a new markOptimisticActive call", () => {
    // store 只保留最近 200 条任务：真实行落地后若被更晚的大量新任务挤出该窗口，
    // 仅靠 markOptimisticActive 内的顺带清理不会再触发——轮询写回本身也要清理，
    // 否则这条已完结的旧标记会永久残留，把资源误判为占用中直到页面刷新。
    useTasksStore.setState({
      tasks: [],
      optimisticActive: new Set([`proj\0character\0A\0image_edit\0${"2025-01-01T00:00:00Z"}`]),
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

  it("keeps a marker whose real row has not landed yet", () => {
    useTasksStore.setState({
      tasks: [],
      optimisticActive: new Set([`proj\0character\0A\0image_edit\0`]),
    });

    useTasksStore.getState().setTasks([
      task({ task_id: "unrelated", resource_id: "B", task_type: "character" }),
    ]);

    expect([...useTasksStore.getState().optimisticActive]).toEqual([
      `proj\0character\0A\0image_edit\0`,
    ]);
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
    it("counts the scriptFile active via an optimistic marker when no real grid row exists yet", () => {
      // 宫格入队成功到轮询写回新 grid 任务行之间的空窗：仅凭乐观标记也应判定本集占用中
      const key = "proj\0grid\0episode_1.json\0";
      expect(
        selectHasActiveTaskForScriptFile([], "grid", "episode_1.json", "proj", new Set([key])),
      ).toBe(true);
    });

    it("lets a real grid row newer than the baseline supersede the marker regardless of its status", () => {
      const key = "proj\0grid\0episode_1.json\0";
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

    it("does not let a stale row predating the baseline supersede a marker for a repeat submission", () => {
      const key = `proj\0grid\0episode_1.json\0${"2026-07-16T00:00:00Z"}`;
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
      const key = "proj\0grid\0episode_1.json\0";
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

describe("useTasksStore.markOptimisticActiveForScriptFile", () => {
  it("marks a scriptFile optimistically active for the given taskType", () => {
    useTasksStore.setState({ tasks: [], optimisticActiveScriptFile: new Set() });

    useTasksStore.getState().markOptimisticActiveForScriptFile("proj", "grid", "episode_1.json");

    const keys = [...useTasksStore.getState().optimisticActiveScriptFile];
    expect(keys).toEqual(["proj\0grid\0episode_1.json\0"]);

    const { tasks, optimisticActiveScriptFile } = useTasksStore.getState();
    expect(
      selectHasActiveTaskForScriptFile(tasks, "grid", "episode_1.json", "proj", optimisticActiveScriptFile),
    ).toBe(true);
  });

  it("normalizes a scripts/ prefix in the scriptFile before storing the key", () => {
    useTasksStore.setState({ tasks: [], optimisticActiveScriptFile: new Set() });

    useTasksStore.getState().markOptimisticActiveForScriptFile("proj", "grid", "scripts/episode_1.json");

    expect([...useTasksStore.getState().optimisticActiveScriptFile]).toEqual([
      "proj\0grid\0episode_1.json\0",
    ]);
  });

  it("prunes markers already superseded by a real row when marking a different scriptFile", () => {
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
      optimisticActiveScriptFile: new Set([`proj\0grid\0episode_1.json\0${"2025-01-01T00:00:00Z"}`]),
    });

    useTasksStore.getState().markOptimisticActiveForScriptFile("proj", "grid", "episode_2.json");

    const keys = [...useTasksStore.getState().optimisticActiveScriptFile];
    expect(keys).toEqual(["proj\0grid\0episode_2.json\0"]);
  });
});

describe("useTasksStore.setTasks prunes stale optimisticActiveScriptFile markers", () => {
  it("removes a scriptFile marker superseded by a real row without a new mark call", () => {
    useTasksStore.setState({
      tasks: [],
      optimisticActiveScriptFile: new Set([`proj\0grid\0episode_1.json\0${"2025-01-01T00:00:00Z"}`]),
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
