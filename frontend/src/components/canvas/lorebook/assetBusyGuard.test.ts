import { afterEach, describe, expect, it } from "vitest";
import { useTasksStore } from "@/stores/tasks-store";
import { makeTask } from "@/test/factories";
import { isAssetBusy } from "./assetBusyGuard";

describe("isAssetBusy", () => {
  afterEach(() => useTasksStore.setState({ tasks: [], optimisticActive: new Set() }));

  it("treats a derivative as busy while its base character is occupied", () => {
    useTasksStore.setState({
      tasks: [makeTask({ project_name: "demo", task_type: "character", resource_id: "林夕", status: "running" })],
    });

    expect(isAssetBusy("character_derivative", "demo", "林夕/战斗装")).toBe(true);
    expect(isAssetBusy("character_derivative", "demo", "苏白/战斗装")).toBe(false);
  });
});
