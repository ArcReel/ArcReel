import { describe, it, expect } from "vitest";
import {
  isActiveStatus,
  isTerminalStatus,
  selectActiveResourceIds,
  selectLatestTaskByResource,
} from "./tasks-store";
import type { TaskItem, TaskStatus } from "@/types";

function task(overrides: Partial<TaskItem> & { task_id: string }): TaskItem {
  return {
    project_name: "proj",
    task_type: "reference_video",
    media_type: "video",
    resource_id: "unit-1",
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
});
