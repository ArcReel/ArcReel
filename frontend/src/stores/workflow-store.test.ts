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
    store.confirmDurations({ E1U1: 8 });
    expect(useWorkflowStore.getState().planKey).toBe("proj::1");

    await useWorkflowStore.getState().refreshPlan("proj", 2);
    expect(useWorkflowStore.getState().planKey).toBe("proj::2");
    expect(useWorkflowStore.getState().confirmedDurations).toEqual({});
  });

  it("首次加载期间切换目标：旧目标的迟到响应作废，不写进 store", async () => {
    const planA = makePlan();
    const planB = makePlan();
    let resolveA!: (plan: ReturnType<typeof makePlan>) => void;
    let resolveB!: (plan: ReturnType<typeof makePlan>) => void;
    vi.spyOn(API, "getWorkflowPlan").mockImplementation((project) =>
      project === "A"
        ? new Promise((resolve) => { resolveA = resolve; })
        : new Promise((resolve) => { resolveB = resolve; }),
    );
    const store = useWorkflowStore.getState();

    const pA = store.refreshPlan("A", 1);
    await Promise.resolve();

    const pB = store.refreshPlan("B", 1);
    await Promise.resolve();

    resolveA(planA);
    expect(await pA).toBe("cancelled");
    await Promise.resolve();
    expect(useWorkflowStore.getState().planKey).not.toBe("A::1");
    expect(useWorkflowStore.getState().plan).not.toEqual(planA);

    resolveB(planB);
    expect(await pB).toBe("success");
    expect(useWorkflowStore.getState().planKey).toBe("B::1");
    expect(useWorkflowStore.getState().plan).toEqual(planB);
  });

  it("running 标志绑定取消域，旧实例的迟到结算不清掉新目标的在途标志（不产生同目标并发请求）", async () => {
    const planA = makePlan();
    const planB = makePlan();
    const pendingByProject: Record<string, Array<(plan: ReturnType<typeof makePlan>) => void>> = {
      A: [],
      B: [],
    };
    let inFlight = 0;
    let maxInFlight = 0;
    const spy = vi.spyOn(API, "getWorkflowPlan").mockImplementation((project: string) => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      return new Promise((resolve) => {
        pendingByProject[project as "A" | "B"].push((plan) => {
          inFlight -= 1;
          resolve(plan);
        });
      });
    });
    const settle = (project: "A" | "B", plan: ReturnType<typeof makePlan>) => {
      const fn = pendingByProject[project].shift();
      fn?.(plan);
    };
    const flush = async (times = 3) => {
      for (let i = 0; i < times; i += 1) await Promise.resolve();
    };

    const store = useWorkflowStore.getState();

    const initial = store.refreshPlan("A", 1);
    await flush();
    settle("A", planA);
    expect(await initial).toBe("success");
    expect(useWorkflowStore.getState().planKey).toBe("A::1");

    const p1 = store.refreshPlan("A", 1);
    await flush();

    const p2 = store.refreshPlan("B", 1);
    await flush();

    settle("A", planA);
    expect(await p1).toBe("cancelled");
    await flush();

    const p3 = store.refreshPlan("B", 1);
    await flush();
    expect(spy).toHaveBeenCalledTimes(3);
    expect(pendingByProject.B).toHaveLength(1);

    settle("B", planB);
    await flush();
    expect(spy).toHaveBeenCalledTimes(4);
    settle("B", planB);

    const [r2, r3] = await Promise.all([p2, p3]);
    expect(r2).toBe("success");
    expect(r3).toBe("success");
    expect(maxInFlight).toBe(2);
  });

  it("被作废的旧实例不消费新取消域下的排队请求（不产生同目标并发请求）", async () => {
    const planA = makePlan();
    const planB1 = makePlan();
    const planB2 = makePlan();
    const pendingByProject: Record<string, Array<(plan: ReturnType<typeof makePlan>) => void>> = {
      A: [],
      B: [],
    };
    let inFlight = 0;
    let maxInFlight = 0;
    const spy = vi.spyOn(API, "getWorkflowPlan").mockImplementation((project: string) => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      return new Promise((resolve) => {
        pendingByProject[project as "A" | "B"].push((plan) => {
          inFlight -= 1;
          resolve(plan);
        });
      });
    });
    const settle = (project: "A" | "B", plan: ReturnType<typeof makePlan>) => {
      const fn = pendingByProject[project].shift();
      fn?.(plan);
    };
    const flush = async (times = 3) => {
      for (let i = 0; i < times; i += 1) await Promise.resolve();
    };

    const store = useWorkflowStore.getState();

    const pA = store.refreshPlan("A", 1);
    await flush();

    const pB1 = store.refreshPlan("B", 1);
    await flush();

    const pB2 = store.refreshPlan("B", 1);
    await flush();
    expect(spy).toHaveBeenCalledTimes(2);

    settle("A", planA);
    expect(await pA).toBe("cancelled");
    await flush();
    expect(spy).toHaveBeenCalledTimes(2);
    expect(maxInFlight).toBe(2);

    settle("B", planB1);
    await flush();
    expect(spy).toHaveBeenCalledTimes(3);
    settle("B", planB2);

    const [rB1, rB2] = await Promise.all([pB1, pB2]);
    expect(rB1).toBe("success");
    expect(rB2).toBe("success");
    expect(useWorkflowStore.getState().plan).toEqual(planB2);
    expect(maxInFlight).toBe(2);
  });

  it("求解带上本次请求的已确认档位", async () => {
    const spy = vi.spyOn(API, "getWorkflowPlan").mockResolvedValue(makePlan());
    const store = useWorkflowStore.getState();
    store.confirmDurations({ E1U1: 8 });
    await useWorkflowStore.getState().refreshPlan("proj", 1);
    expect(spy).toHaveBeenCalledWith(
      "proj",
      { episode_id: 1, confirmed_request_durations: { E1U1: 8 } },
      expect.anything(),
    );
  });
});
