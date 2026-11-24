import { beforeEach, describe, it, expect, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { createDeferred } from "@/test/deferred";
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

const TASK_ID_SEP = "\u0001";

function pendingKey(
  resourceKind: string,
  resourceId: string,
  pendingTaskType: string,
  opts: { projectName?: string; seq?: number } = {},
): string {
  const { projectName = "proj", seq = 1 } = opts;
  return `${projectName}\0${resourceKind}\0${resourceId}\0${pendingTaskType}\0${seq}\0`;
}

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
    const inactive: TaskStatus[] = ["succeeded", "failed", "cancelled"];
    for (const status of inactive) expect(isActiveStatus(status)).toBe(false);
  });
});

describe("isOccupyingStatus", () => {
  it("counts queued/running as occupying", () => {
    expect(isOccupyingStatus("queued")).toBe(true);
    expect(isOccupyingStatus("running")).toBe(true);
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
    const key = pendingKey("character", "A", "image_edit");
    expect(selectActiveResourceIds([], "character", "proj", new Set([key])).has("A")).toBe(true);
  });

  it("lets the marker's own task row supersede it regardless of that row's status", () => {
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
      ).toBe(true);

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
      ).toBe(false);
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

    useTasksStore.getState().setTasks([row("grid-2", "grid-def")]);
    expect([...useTasksStore.getState().optimisticActiveScriptFile]).toEqual([]);
  });

  it("一次提交建多条任务行时，只落地其中一条不让位", () => {
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
    const tasks = [
      task({ task_id: "old", resource_id: "u1", status: "running", updated_at: "2026-07-16T00:00:00Z" }),
      task({ task_id: "new", resource_id: "u1", status: "failed", updated_at: "2026-07-16T01:00:00Z" }),
    ];
    expect(selectActiveResourceIds(tasks, "reference_video", "proj").has("u1")).toBe(false);
  });

  it("reports a retry active when its newest row is running despite an older failed row", () => {
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
});

describe("isResourceBusy", () => {
  beforeEach(() => {
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
  });

  it("reads the store snapshot at call time rather than a captured one", () => {
    expect(isResourceBusy("reference_video", "proj", "u1")).toBe(false);
    useTasksStore.setState({ tasks: [task({ task_id: "a", resource_id: "u1", status: "running" })] });
    expect(isResourceBusy("reference_video", "proj", "u1")).toBe(true);
  });

  it("counts the optimistic in-flight markers held in the store", () => {
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
    expect(isScriptFileBusy("grid", "episode_1.json", "proj")).toBe(false);
    useTasksStore.setState({
      tasks: [
        task({ task_id: "g1", task_type: "grid", script_file: "episode_1.json", status: "running" }),
      ],
    });
    expect(isScriptFileBusy("grid", "episode_1.json", "proj")).toBe(true);
  });

  it("normalizes the scripts/ prefix on both sides", () => {
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
    useAppStore.setState({ referenceVideoUnitsRevision: 0 });
    useTasksStore.getState().setRefreshScope({ projectName: "proj" });

    mockFetch([task({ task_id: "rv1", task_type: "reference_video", status: "running" })]);
    await useTasksStore.getState().refreshTasks();
    expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(0);

    vi.restoreAllMocks();
    mockFetch([task({ task_id: "rv1", task_type: "reference_video", status: "succeeded" })]);
    await useTasksStore.getState().refreshTasks();
    expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(1);

    await useTasksStore.getState().refreshTasks();
    expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(1);
  });

  it("轮询看到参考单元旁白配音任务转成功时失效单元缓存", async () => {
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
        succeeded: 0,
        failed: 0,
        cancelled: 0,
        total: 0,
      },
    });
    useTasksStore.getState().setRefreshScope({ projectName: "old-project" });

    const pending = useTasksStore.getState().refreshTasks();
    await Promise.resolve();
    useTasksStore.getState().setRefreshScope({ projectName: "new-project" });
    releases[0]([task({ task_id: "stale" })]);
    await pending;

    expect(useTasksStore.getState().tasks).toEqual([]);
  });

  it("discards an old refresh after leaving and returning to the same project", async () => {
    const fresh = task({ task_id: "fresh" });
    const stale = task({ task_id: "stale" });
    const oldList = createDeferred<Awaited<ReturnType<typeof API.listTasks>>>();
    mockFetch([fresh]).mockReturnValueOnce(oldList.promise);
    const lookup = vi.spyOn(API, "getTask").mockResolvedValue(
      task({ task_id: "sample", task_type: "voice_sample", resource_id: "A", status: "succeeded" }),
    );
    const { beginOptimisticActive, refreshTasks, setRefreshScope } = useTasksStore.getState();
    beginOptimisticActive("proj", "character", "A", "voice_sample").settle(["sample"]);
    const snapshots: string[][] = [];
    const unsubscribe = useTasksStore.subscribe(({ tasks }, previous) => {
      if (tasks !== previous.tasks) snapshots.push(tasks.map(({ task_id }) => task_id));
    });

    setRefreshScope({ projectName: "proj" });
    const previous = refreshTasks();
    setRefreshScope({ projectName: "other" });
    setRefreshScope({ projectName: "proj" });
    const next = refreshTasks();
    oldList.resolve({ items: [stale], total: 1, page: 1, page_size: 200 });
    try {
      await Promise.all([previous, next]);
      expect(snapshots.filter((ids) => ids.includes(stale.task_id))).toEqual([]);
      expect(useTasksStore.getState().tasks).toEqual([fresh]);
      expect(lookup).toHaveBeenCalledOnce();
    } finally {
      unsubscribe();
    }
  });

  it("releases a settled voice sample while another lookup is still pending", async () => {
    mockFetch();
    const ready = task({ task_id: "ready", task_type: "voice_sample", resource_id: "A", status: "succeeded" });
    const slow = task({ task_id: "slow", task_type: "voice_sample", resource_id: "B", status: "running" });
    const pending = createDeferred<TaskItem>();
    vi.spyOn(API, "getTask").mockImplementation(
      (taskId) => taskId === ready.task_id ? Promise.resolve(ready) : pending.promise,
    );
    const { beginOptimisticActive, refreshTasks, setRefreshScope } = useTasksStore.getState();
    beginOptimisticActive("proj", "character", "A", "voice_sample").settle([ready.task_id]);
    beginOptimisticActive("proj", "character", "B", "voice_sample").settle([slow.task_id]);
    setRefreshScope({ projectName: "proj" });

    vi.useFakeTimers();
    const refresh = refreshTasks();
    try {
      await vi.advanceTimersByTimeAsync(0);
      expect(isResourceBusy("character", "proj", "A")).toBe(false);
      expect(isResourceBusy("character", "proj", "B")).toBe(true);
    } finally {
      pending.resolve(slow);
      await refresh;
      vi.useRealTimers();
    }
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

  it("clears snapshot occupancy for ordinary tasks and untracked voice samples when the list empties", async () => {
    const listSpy = mockFetch([
      task({ task_id: "image-1", task_type: "character", resource_id: "A", status: "running" }),
      task({ task_id: "sample-1", task_type: "voice_sample", resource_id: "B", status: "running" }),
    ]);
    const lookup = vi.spyOn(API, "getTask");
    const { beginOptimisticActive, refreshTasks, setRefreshScope } = useTasksStore.getState();
    beginOptimisticActive("proj", "character", "A", "character").settle(["image-1"]);
    setRefreshScope({ projectName: "proj" });
    await refreshTasks();
    expect(isResourceBusy("character", "proj", "A")).toBe(true);
    expect(isResourceBusy("character", "proj", "B")).toBe(true);

    listSpy.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 200 });
    await refreshTasks();
    expect(isResourceBusy("character", "proj", "A")).toBe(false);
    expect(isResourceBusy("character", "proj", "B")).toBe(false);
    expect(lookup).not.toHaveBeenCalled();
  });

  it.each(["switches projects", "resets the scope"])(
    "cancels a stalled reconciliation before refreshing the next project when it %s",
    async (transition) => {
      const oldSample = task({
        task_id: "old-sample", task_type: "voice_sample", resource_id: "A", status: "running",
      });
      const newTask = task({ task_id: "new-task", project_name: "next", status: "succeeded" });
      const list = mockFetch();
      list.mockResolvedValueOnce({ items: [], total: 0, page: 1, page_size: 200 });
      list.mockResolvedValue({ items: [newTask], total: 1, page: 1, page_size: 200 });
      const stats = { ...defaultTaskStats, succeeded: 1, total: 1 };
      vi.mocked(API.getTaskStats).mockResolvedValueOnce({ stats: defaultTaskStats });
      vi.mocked(API.getTaskStats).mockResolvedValue({ stats });
      const stalled = createDeferred<TaskItem>();
      const started = createDeferred<AbortSignal | undefined>();
      const lookup = vi.spyOn(API, "getTask").mockImplementation((_id, options) => {
        started.resolve(options?.signal);
        return stalled.promise;
      });
      const { beginOptimisticActive, refreshTasks, setRefreshScope, setTasks, setStats, setConnected } =
        useTasksStore.getState();
      beginOptimisticActive("proj", "character", "A", "voice_sample").settle([oldSample.task_id]);
      setRefreshScope({ projectName: "proj" });

      vi.useFakeTimers();
      const previous = refreshTasks();
      let next: Promise<void> | undefined;
      try {
        const signal = await started.promise;
        setRefreshScope({ projectName: "proj" });
        expect(signal?.aborted).toBe(false);
        const resetsScope = transition === "resets the scope";
        if (resetsScope) {
          setRefreshScope(null);
          setTasks([]);
          setStats(defaultTaskStats);
          setConnected(false);
        }
        expect(signal?.aborted).toBe(resetsScope);
        setRefreshScope({ projectName: "next" });
        next = refreshTasks();
        await vi.advanceTimersByTimeAsync(0);

        expect(signal?.aborted).toBe(true);
        expect(useTasksStore.getState().tasks).toEqual([newTask]);
        expect(useTasksStore.getState().stats).toEqual(stats);
        expect(useTasksStore.getState().connected).toBe(true);
        await Promise.all([previous, next]);
        expect(lookup).toHaveBeenCalledOnce();

        stalled.resolve({ ...oldSample, status: "succeeded" });
        await vi.advanceTimersByTimeAsync(0);
        expect(isResourceBusy("character", "proj", "A")).toBe(true);
      } finally {
        stalled.resolve(oldSample);
        await Promise.all([previous, next]);
        vi.useRealTimers();
      }
    },
  );

  it("continues refreshing after a missing voice sample lookup times out", async () => {
    const running = task({
      task_id: "sample-1",
      task_type: "voice_sample",
      media_type: "audio",
      resource_id: "A",
      status: "running",
    });
    const newer = task({ task_id: "newer", status: "succeeded" });
    const listSpy = mockFetch();
    listSpy.mockResolvedValueOnce({ items: [], total: 0, page: 1, page_size: 200 });
    listSpy.mockResolvedValueOnce({ items: [newer], total: 1, page: 1, page_size: 200 });
    const pending = createDeferred<TaskItem>();
    const lookup = vi.spyOn(API, "getTask")
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValue(running);
    const { beginOptimisticActive, refreshTasks, setRefreshScope } = useTasksStore.getState();
    beginOptimisticActive("proj", "character", "A", "voice_sample").settle([running.task_id]);
    setRefreshScope({ projectName: "proj" });

    vi.useFakeTimers();
    const first = refreshTasks();
    const queued = refreshTasks();
    try {
      await vi.advanceTimersByTimeAsync(10_000);
      expect(listSpy).toHaveBeenCalledTimes(2);
      await Promise.all([first, queued]);
      expect(lookup.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
      expect(lookup.mock.calls.map(([id]) => id)).toEqual([running.task_id, running.task_id]);
      expect(useTasksStore.getState().tasks).toEqual([newer]);
      expect(useTasksStore.getState().connected).toBe(true);

      pending.resolve({ ...running, status: "succeeded" });
      await vi.advanceTimersByTimeAsync(0);
      expect(isResourceBusy("character", "proj", "A")).toBe(true);
    } finally {
      pending.resolve(running);
      await Promise.all([first, queued]);
      vi.useRealTimers();
    }
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
      selectNeedsFastPolling({ ...idle, stats: { ...defaultTaskStats, running: 1 } }),
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
