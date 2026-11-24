import { seedTaskState } from "@/test/task-state";
import { afterEach, describe, expect, it } from "vitest";
import { makeTask } from "@/test/factories";
import { isAssetBusy } from "./assetBusyGuard";

describe("isAssetBusy", () => {
  afterEach(() => seedTaskState({ tasks: [], optimisticMarks: new Map() }));

  it("treats a derivative as busy while its base character is occupied", () => {
    seedTaskState({
      tasks: [makeTask({ project_name: "demo", task_type: "character", resource_id: "林夕", status: "running" })],
    });

    expect(isAssetBusy("derivative_image", "character", "demo", "林夕/战斗装")).toBe(true);
    expect(isAssetBusy("derivative_image", "character", "demo", "苏白/战斗装")).toBe(false);
  });
});
