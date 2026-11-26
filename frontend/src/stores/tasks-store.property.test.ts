import * as fc from "fast-check";
import { expect, it, vi } from "vitest";
import { API } from "@/api";
import { makeTask } from "@/test/factories";
import { commandLimits, propertyParameters, propertyTimeout } from "@/test/property";
import { defaultTaskStats, isResourceBusy, useTasksStore } from "./tasks-store";

const state = useTasksStore.getState;
const running = makeTask({ task_type: "voice_sample", media_type: "audio", status: "running" });
const terminal = { ...running, status: "succeeded" as const, updated_at: "2026-04-20T00:01:00Z" };
const busy = () => isResourceBusy("voice_sample", running.project_name, running.resource_id);

it("keeps a settled task released across stale active, recent and tracked snapshots", { timeout: propertyTimeout }, async () => {
  vi.spyOn(API, "getActiveTasks");
  vi.spyOn(API, "listTasks");
  vi.spyOn(API, "lookupTasks");
  vi.spyOn(API, "getTaskStats").mockResolvedValue({ stats: defaultTaskStats });

  const snapshot = fc.record({
    active: fc.boolean(),
    truncated: fc.boolean(),
    recent: fc.constantFrom("off-page", "running", "terminal"),
    lookup: fc.constantFrom("running", "terminal"),
  }).map((step): fc.AsyncCommand<{ settled: boolean }, typeof state> => ({
    check: () => true,
    async run(model, getStore) {
      vi.mocked(API.getActiveTasks).mockResolvedValue({ items: step.active ? [running] : [], truncated: step.truncated });
      const items = step.recent === "off-page" ? [] : [step.recent === "running" ? running : terminal];
      vi.mocked(API.listTasks).mockResolvedValue({ items, page: 1, page_size: 200, total: items.length });
      const needsLookup = !model.settled && !step.active && step.recent !== "terminal";
      let lookedUp = false;
      vi.mocked(API.lookupTasks).mockImplementation(async (ids) => {
        lookedUp = ids.includes(running.task_id);
        return { items: [step.lookup === "running" ? running : terminal], missing_ids: [] };
      });

      await getStore().refreshTasks();
      expect(needsLookup && !lookedUp, "pending off-page task missed its lookup").toBe(false);
      model.settled ||= step.recent === "terminal" || (lookedUp && step.lookup === "terminal");
      expect(getStore().connected).toBe(true);
      expect(getStore().tracked.get(running.task_id)?.status).toBe(model.settled ? "resolved" : "pending");
      expect(busy()).toBe(!model.settled);
    },
    toString: () => JSON.stringify(step),
  }));

  await fc.assert(fc.asyncProperty(fc.commands([snapshot], commandLimits), async (commands) => {
    vi.clearAllMocks();
    state().setRefreshScope(null);
    state().setRefreshScope({ projectName: running.project_name });
    state().retainTask(running.project_name, running.task_id);
    state().beginOptimisticActive(running.project_name, "voice_sample", running.resource_id, running.task_type)
      .settle([running.task_id]);
    try {
      await fc.asyncModelRun(() => ({ model: { settled: false }, real: state }), commands);
    } finally {
      state().setRefreshScope(null);
    }
  }), propertyParameters("src/stores/tasks-store.property.test.ts"));
});
