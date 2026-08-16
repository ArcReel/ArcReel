import { describe, it, expect, vi, beforeEach } from "vitest";
import { API } from "@/api";
import { useWorkflowStore } from "./workflow-store";
import { makePlan } from "@/test/factories";

beforeEach(() => {
  useWorkflowStore.getState().resetTarget();
});

describe("workflow-store", () => {
  it("同一目标的并发刷新合并为两轮，不各自发一串请求", async () => {
    const spy = vi.spyOn(API, "getWorkflowPlan").mockResolvedValue(makePlan());
    const store = useWorkflowStore.getState();
    const results = await Promise.all([
      store.refreshPlan("proj", 1),
      store.refreshPlan("proj", 1),
      store.refreshPlan("proj", 1),
    ]);
    expect(results).toEqual(["success", "success", "success"]);
    // 首轮 + 合并后的一轮补跑，而不是三轮
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("刷新失败保留上一次的计划，只写错误", async () => {
    const plan = makePlan();
    const spy = vi.spyOn(API, "getWorkflowPlan").mockResolvedValue(plan);
    await useWorkflowStore.getState().refreshPlan("proj", 1);
    expect(useWorkflowStore.getState().plan).toEqual(plan);

    spy.mockRejectedValueOnce(new Error("offline"));
    expect(await useWorkflowStore.getState().refreshPlan("proj", 1)).toBe("failed");
    expect(useWorkflowStore.getState().plan).toEqual(plan);
    expect(useWorkflowStore.getState().error).toBe("offline");
  });

  it("换目标时清掉上一个目标的计划与本次请求选择", async () => {
    vi.spyOn(API, "getWorkflowPlan").mockResolvedValue(makePlan());
    const store = useWorkflowStore.getState();
    await store.refreshPlan("proj", 1);
    store.setNarrationDelivery("use_tts");
    expect(useWorkflowStore.getState().planKey).toBe("proj::1");

    await useWorkflowStore.getState().refreshPlan("proj", 2);
    expect(useWorkflowStore.getState().planKey).toBe("proj::2");
    // 旁白交付是「本次请求」的选择，换目标即作废，不跟着走到下一集
    expect(useWorkflowStore.getState().narrationDelivery).toBeNull();
  });

  it("求解带上本次请求的交付选择与已确认档位", async () => {
    const spy = vi.spyOn(API, "getWorkflowPlan").mockResolvedValue(makePlan());
    const store = useWorkflowStore.getState();
    store.setNarrationDelivery("post_production");
    store.confirmDurations({ E1U1: 8 });
    await useWorkflowStore.getState().refreshPlan("proj", 1);
    expect(spy).toHaveBeenCalledWith(
      "proj",
      { episode: 1, narration_delivery: "post_production", confirmed_request_durations: { E1U1: 8 } },
      expect.anything(),
    );
  });
});
