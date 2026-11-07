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
    // Initial plus one merged follow-up, never three rounds.
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
    // Confirmed tiers belong to this request; target changes invalidate them rather than follow into later episodes.
    expect(useWorkflowStore.getState().confirmedDurations).toEqual({});
  });

  it("首次加载期间切换目标：旧目标的迟到响应作废，不写进 store", async () => {
    // planKey starts null and updates only after success. Using planKey alone to detect target changes
    // makes initial-load A→B switches see null twice and falsely retain ownership,
    // persisting late A responses into current-target plan/planKey.
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

    // A::1 is this session's first request, with null planKey.
    const pA = store.refreshPlan("A", 1);
    await Promise.resolve();

    // Switch to B before A persists, reproducing fast episode switches during initial loading.
    const pB = store.refreshPlan("B", 1);
    await Promise.resolve();

    // Late A responses must settle cancelled without overwriting stores.
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
    // Reproduction: A::1 succeeded; same-target refresh leaves resetTarget untouched,
    // running=true and instances bound to current cancellation domains. Switch to B: resetTarget rotates domains,
    // explicitly resets running and starts new-domain instances. Late old A responses then
    // settle cancelled by abort; unconditional running cleanup while B is unfinished
    // and store.planKey still "A::1" lets the next A refresh falsely see
    // running===false, issuing a second real request concurrently with in-flight B.
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

    // 1) Establish A::1 with one successful plan.
    const initial = store.refreshPlan("A", 1);
    await flush();
    settle("A", planA);
    expect(await initial).toBe("success");
    expect(useWorkflowStore.getState().planKey).toBe("A::1");

    // 2) Refresh same targets: keys match persisted planKey, skipping resetTarget;
    //    running=true, instances bound to current cancellation domains.
    const p1 = store.refreshPlan("A", 1);
    await flush();

    // 3) Switch to B: resetTarget rotates domains/resets running, starts new-domain instances.
    const p2 = store.refreshPlan("B", 1);
    await flush();

    // 4) Old step-2 instances settle late after resetTarget invalidated their domains;
    //    classify cancelled, never clear still-running step-3 instance running state on exit.
    settle("A", planA);
    expect(await p1).toBe("cancelled");
    await flush();

    // 5) Current target B (currentTarget from step 3) refreshes through same-target
    //    merging, never resetTarget. Incorrect step-4 running cleanup would immediately
    //    issue second B requests alongside step 3. Without erroneous cleanup,
    //    queue/merge until step 3 finishes before issuing requests.
    const p3 = store.refreshPlan("B", 1);
    await flush();
    // Key assertion: before step-3 settlement, no extra real requests exist.
    expect(spy).toHaveBeenCalledTimes(3);
    expect(pendingByProject.B).toHaveLength(1);

    settle("B", planB);
    await flush();
    // Only after step 3 ends, follow-up B requests run as the fourth real request.
    expect(spy).toHaveBeenCalledTimes(4);
    settle("B", planB);

    const [r2, r3] = await Promise.all([p2, p3]);
    expect(r2).toBe("success");
    expect(r3).toBe("success");
    // At most one real network request is in flight: during step-2/3 handoff, old-instance
    // requests already aborted but responses not settled are normal network cleanup windows,
    // outside the concurrent-duplicate-request invariant guarded here.
    expect(maxInFlight).toBe(2);
  });

  it("被作废的旧实例不消费新取消域下的排队请求（不产生同目标并发请求）", async () => {
    // Reproduction: while A is in flight, switch to B (resetTarget rotates domains, starts successors);
    // immediately refresh B again, queueing behind running successors. Late old A instances
    // then settle. Without checking current cancellation-domain ownership before consuming queues,
    // old instances steal queued B requests from successors and rebind to current
    // scopes, issuing second B requests concurrently with successors' own ongoing requests.
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

    // 1) Start A in flight on initial loads, currentTarget=A.
    const pA = store.refreshPlan("A", 1);
    await flush();

    // 2) Switch to B: resetTarget invalidates A domains, starts successors (second real request).
    const pB1 = store.refreshPlan("B", 1);
    await flush();

    // 3) Immediately refresh B: successors still run, queue without third real requests.
    const pB2 = store.refreshPlan("B", 1);
    await flush();
    expect(spy).toHaveBeenCalledTimes(2);

    // 4) Old A instances settle late: cancelled, never stealing step-3 queues.
    settle("A", planA);
    expect(await pA).toBe("cancelled");
    await flush();
    // Key assertion: old-instance settlement must not add real requests; queued B work
    // belongs only to successors serving step 2, after their own request completion.
    expect(spy).toHaveBeenCalledTimes(2);
    expect(maxInFlight).toBe(2);

    // 5) Successors complete, then run queued B follow-ups (third real request).
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
