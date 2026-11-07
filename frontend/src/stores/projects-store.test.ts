import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { DEMO_PROJECT_NAME } from "@/onboarding/demo-project";
import type { ProjectData } from "@/types";
import { createDeferred } from "@/test/deferred";

type GetProjectResult = Awaited<ReturnType<typeof API.getProject>>;

function makeProject(title: string): ProjectData {
  return {
    title,
    content_mode: "narration",
    style: "Anime",
    episodes: [],
    characters: {},
    scenes: {},
    props: {},
  };
}

function makeResult(title: string, fingerprints: Record<string, number> = {}): GetProjectResult {
  return { project: makeProject(title), scripts: {}, asset_fingerprints: fingerprints };
}

// Flush microtask/timer queues, advancing in-flight refresh continuations to their next await.
const flush = () => new Promise((r) => setTimeout(r, 0));

describe("projects-store refreshProject", () => {
  beforeEach(() => {
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useAppStore.setState(useAppStore.getInitialState(), true);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("空 name 直接返回 cancelled，不发请求", async () => {
    const spy = vi.spyOn(API, "getProject");
    const result = await useProjectsStore.getState().refreshProject("");
    expect(result).toBe("cancelled");
    expect(spy).not.toHaveBeenCalled();
  });

  it("成功时写入 currentProject 并返回 success", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue(makeResult("Demo", { "a.png": 1 }));
    const result = await useProjectsStore.getState().refreshProject("demo");
    expect(result).toBe("success");
    const s = useProjectsStore.getState();
    expect(s.currentProjectName).toBe("demo");
    expect(s.currentProjectData?.title).toBe("Demo");
    expect(s.getAssetFingerprint("a.png")).toBe(1);
    expect(s.projectSnapshotRevisions.demo).toBe(1);
  });

  it("同一项目每次完整快照落定时推进其独立修订", () => {
    const store = useProjectsStore.getState();
    store.setCurrentProject("A", null);
    expect(useProjectsStore.getState().projectSnapshotRevisions).toEqual({});

    store.setCurrentProject("A", makeProject("A-1"), {}, {});
    store.setCurrentProject("A", makeProject("A-2"), {}, {});
    store.setCurrentProject("B", null);
    store.setCurrentProject("B", makeProject("B-1"), {}, {});

    expect(useProjectsStore.getState().projectSnapshotRevisions).toEqual({ A: 2, B: 1 });
  });

  it("成功后按 invalidateKeys 失效实体版本", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue(makeResult("Demo"));
    const result = await useProjectsStore
      .getState()
      .refreshProject("demo", { invalidateKeys: ["segment:S1", "character:hero"] });
    expect(result).toBe("success");
    const app = useAppStore.getState();
    expect(app.getEntityRevision("segment:S1")).toBe(1);
    expect(app.getEntityRevision("character:hero")).toBe(1);
  });

  it("失败留旧：不覆盖 currentProjectData，返回 failed，onError 收到错误", async () => {
    useProjectsStore.getState().setCurrentProject("demo", makeProject("旧"), {}, {});
    const err = new Error("boom");
    vi.spyOn(API, "getProject").mockRejectedValue(err);
    const onError = vi.fn();
    const result = await useProjectsStore.getState().refreshProject("demo", { onError });
    expect(result).toBe("failed");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("旧");
    expect(onError).toHaveBeenCalledWith(err);
  });

  it("在途合并：在途期间的多次请求只多触发一次 getProject，最终反映最新一轮", async () => {
    const d1 = createDeferred<GetProjectResult>();
    const d2 = createDeferred<GetProjectResult>();
    const spy = vi
      .spyOn(API, "getProject")
      .mockReturnValueOnce(d1.promise)
      .mockReturnValueOnce(d2.promise);

    const store = useProjectsStore.getState();
    const p1 = store.refreshProject("demo"); // Owner starts the first round.
    const p2 = store.refreshProject("demo"); // In flight → merge.
    const p3 = store.refreshProject("demo"); // In flight → merge.
    // Only initial requests issued while merging.
    expect(spy).toHaveBeenCalledTimes(1);

    d1.resolve(makeResult("R1"));
    await flush();
    // Queued requests consolidate into one follow-up, now started.
    expect(spy).toHaveBeenCalledTimes(2);

    d2.resolve(makeResult("R2"));
    const [r1, r2, r3] = await Promise.all([p1, p2, p3]);
    expect([r1, r2, r3]).toEqual(["success", "success", "success"]);
    // Three refresh intents merge into two requests; stores settle on latest results.
    expect(spy).toHaveBeenCalledTimes(2);
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("R2");
  });

  it("首轮失败、排队轮成功时用新值替换旧值；各调用方返回自己那一轮的结果", async () => {
    useProjectsStore.getState().setCurrentProject("demo", makeProject("旧"), {}, {});
    const d1 = createDeferred<GetProjectResult>();
    const d2 = createDeferred<GetProjectResult>();
    vi.spyOn(API, "getProject").mockReturnValueOnce(d1.promise).mockReturnValueOnce(d2.promise);

    const store = useProjectsStore.getState();
    const p1 = store.refreshProject("demo");
    const p2 = store.refreshProject("demo"); // Merge → one follow-up after completion.

    d1.reject(new Error("first fail"));
    await flush();
    // First rounds fail, retaining old data.
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("旧");

    d2.resolve(makeResult("新"));
    const [r1, r2] = await Promise.all([p1, p2]);
    // Initial callers receive their own failed outcomes, never overwritten by later queued success;
    // queued callers receive their own successful outcomes.
    expect(r1).toBe("failed");
    expect(r2).toBe("success");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("新");
  });

  it("首轮成功、排队轮失败时，首轮调用方仍返回 success（不被无关的后续轮次拖累）", async () => {
    const d1 = createDeferred<GetProjectResult>();
    const d2 = createDeferred<GetProjectResult>();
    vi.spyOn(API, "getProject").mockReturnValueOnce(d1.promise).mockReturnValueOnce(d2.promise);

    const store = useProjectsStore.getState();
    // For example handleMoveShot refreshes after reordering, using return values to advance selections.
    const pMoveShot = store.refreshProject("demo");
    // Another merged intent (for example SSE refresh) later fails.
    const pSse = store.refreshProject("demo");

    d1.resolve(makeResult("重排后"));
    await flush();
    // Initial rounds already persisted successfully.
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("重排后");

    d2.reject(new Error("sse round fail"));
    const [okMoveShot, okSse] = await Promise.all([pMoveShot, pSse]);
    // Initial callers receive their own success, never overwritten by later queued failures.
    expect(okMoveShot).toBe("success");
    expect(okSse).toBe("failed");
    // Retain on failure: stores preserve initial successful data without clearing/rolling back after queued failures.
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("重排后");
  });

  it("排队期间被更晚的不同项目请求取代：被取代的调用方立即收到 cancelled，不与新项目的结果混同", async () => {
    const dA = createDeferred<GetProjectResult>();
    const dC = createDeferred<GetProjectResult>();
    vi.spyOn(API, "getProject").mockReturnValueOnce(dA.promise).mockReturnValueOnce(dC.promise);

    const store = useProjectsStore.getState();
    const pA = store.refreshProject("A"); // Starting.
    const pB = store.refreshProject("B"); // Queued → queuedName = B.
    const pC = store.refreshProject("C"); // Different projects arrive while queued: C replaces B.

    // B callers receive cancelled immediately on replacement, without waiting for A/C settlement:
    // requested projects never actually fetched must not share C outcomes.
    const okB = await pB;
    expect(okB).toBe("cancelled");
    // Only A requested; B was replaced before its turn and issues nothing.
    expect(API.getProject).toHaveBeenCalledTimes(1);

    dA.resolve(makeResult("A-数据"));
    await flush();
    // A responses arrive with C queued: skip commits (existing behavior), C requests started.
    expect(useProjectsStore.getState().currentProjectData?.title).not.toBe("A-数据");
    expect(API.getProject).toHaveBeenCalledTimes(2);

    dC.resolve(makeResult("C-数据"));
    const [okA, okC] = await Promise.all([pA, pC]);
    expect(okA).toBe("cancelled");
    expect(okC).toBe("success");
    expect(useProjectsStore.getState().currentProjectName).toBe("C");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("C-数据");
  });

  it("跨项目合并：A 在途时排队刷新 B，A 的响应不写入 store（避免覆盖排队中的 B）", async () => {
    useProjectsStore.getState().setCurrentProject("B", makeProject("B-旧"), {}, {});
    const dA = createDeferred<GetProjectResult>();
    const dB = createDeferred<GetProjectResult>();
    vi.spyOn(API, "getProject").mockReturnValueOnce(dA.promise).mockReturnValueOnce(dB.promise);

    const store = useProjectsStore.getState();
    const pA = store.refreshProject("A");
    const pB = store.refreshProject("B"); // Merge → different queued names.

    dA.resolve(makeResult("A-数据"));
    await flush();
    // A responses arrive with different project B queued: skip commits, preserving old B data.
    expect(useProjectsStore.getState().currentProjectName).toBe("B");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("B-旧");

    dB.reject(new Error("B failed"));
    const [okA, okB] = await Promise.all([pA, pB]);
    expect(okA).toBe("cancelled");
    expect(okB).toBe("failed");
    // B rounds fail: retain old B data, never change to A data.
    expect(useProjectsStore.getState().currentProjectName).toBe("B");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("B-旧");
  });

  it("排队轮 onError：首轮无回调、排队轮有回调且失败时通知排队轮回调", async () => {
    const d1 = createDeferred<GetProjectResult>();
    const d2 = createDeferred<GetProjectResult>();
    vi.spyOn(API, "getProject").mockReturnValueOnce(d1.promise).mockReturnValueOnce(d2.promise);
    const onError2 = vi.fn();

    const store = useProjectsStore.getState();
    const p1 = store.refreshProject("demo"); // Initial rounds lack onError.
    const p2 = store.refreshProject("demo", { onError: onError2 }); // Queued rounds include onError.

    d1.resolve(makeResult("R1"));
    await flush();
    const err = new Error("round2 fail");
    d2.reject(err);
    await Promise.all([p1, p2]);
    expect(onError2).toHaveBeenCalledWith(err);
  });

  it("合并期间累积 invalidateKeys：排队轮成功后一并失效", async () => {
    const d1 = createDeferred<GetProjectResult>();
    const d2 = createDeferred<GetProjectResult>();
    vi.spyOn(API, "getProject").mockReturnValueOnce(d1.promise).mockReturnValueOnce(d2.promise);

    const store = useProjectsStore.getState();
    const p1 = store.refreshProject("demo", { invalidateKeys: ["segment:S1"] });
    const p2 = store.refreshProject("demo", { invalidateKeys: ["segment:S2"] });

    d1.resolve(makeResult("R1"));
    await flush();
    d2.resolve(makeResult("R2"));
    await Promise.all([p1, p2]);

    const app = useAppStore.getState();
    // Initial rounds invalidate S1; queued rounds include S2, never count S1 again.
    expect(app.getEntityRevision("segment:S1")).toBe(1);
    expect(app.getEntityRevision("segment:S2")).toBe(1);
  });

  it("真实项目的刷新在只读演示项目接管当前视图后落定：不覆盖回真实项目", async () => {
    // Scenario: SSE onChanges starts real-project refreshProject; users navigate into readonly demos
    // while requests are in flight. Demos bypass refreshProject, directly calling
    // setCurrentProject with frontend constants. Late real-project responses must not restore
    // real currentProjectName, silently replacing readonly banners/demo data.
    const d = createDeferred<GetProjectResult>();
    vi.spyOn(API, "getProject").mockReturnValueOnce(d.promise);

    const store = useProjectsStore.getState();
    const p = store.refreshProject("real-project");

    store.setCurrentProject(DEMO_PROJECT_NAME, makeProject("演示"), {}, {});

    d.resolve(makeResult("迟到的真实数据"));
    const ok = await p;

    expect(ok).toBe("cancelled");
    expect(useProjectsStore.getState().currentProjectName).toBe(DEMO_PROJECT_NAME);
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("演示");
  });

  it("项目切换后 A 的迟到响应落定：不覆盖已接管的 B", async () => {
    // Route project switches bypass refreshProject through their own AbortController + setCurrentProject,
    // invisible to queue deduplication. A-page SSE/postwrite refreshes settling after B switches
    // must not restore currentProjectName to A.
    useProjectsStore.getState().setCurrentProject("A", makeProject("A-数据"), {}, {});
    const dA = createDeferred<GetProjectResult>();
    vi.spyOn(API, "getProject").mockReturnValueOnce(dA.promise);

    const store = useProjectsStore.getState();
    const pA = store.refreshProject("A");

    // Route switches to B: cleanup clears current projects, then writes B data.
    store.setCurrentProject(null, null);
    store.setCurrentProject("B", makeProject("B-数据"), {}, {});

    dA.resolve(makeResult("A-迟到数据"));
    const ok = await pA;

    expect(ok).toBe("cancelled");
    expect(useProjectsStore.getState().currentProjectName).toBe("B");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("B-数据");
  });

  it("null 过渡窗口内发起的旧项目刷新：不豁免为「无当前项目」，不写回", async () => {
    // Codex scenario: router cleanup sets currentProjectName null, with asynchronous windows before B's
    // getProject settles/writes currentProjectName. Uncancelled A writes can start
    // refreshProject("A") during this window, receiving cleared active un-aborted
    // domains with null currentProjectName. No-current-project exemptions must not permit them, or
    // A data writes before B arrives.
    useProjectsStore.getState().setCurrentProject("A", makeProject("A-数据"), {}, {});
    useProjectsStore.getState().setCurrentProject(null, null); // Router cleanup: cleared, B not yet loaded.

    const dA = createDeferred<GetProjectResult>();
    vi.spyOn(API, "getProject").mockReturnValueOnce(dA.promise);

    const pA = useProjectsStore.getState().refreshProject("A"); // Invoke during null windows.
    dA.resolve(makeResult("A-迟到数据"));
    const ok = await pA;

    expect(ok).toBe("cancelled");
    expect(useProjectsStore.getState().currentProjectName).toBe(null);
    expect(useProjectsStore.getState().currentProjectData).toBe(null);

    // B later persists, unaffected by blocked A writes.
    useProjectsStore.getState().setCurrentProject("B", makeProject("B-数据"), {}, {});
    expect(useProjectsStore.getState().currentProjectName).toBe("B");
  });

  it("跨两次切换的更早项目迟到写入（C→A→B）：null 窗口不因换了新名字而放行", async () => {
    // Codex multihop scenario after the preceding fix: uncancelled C-page writes start refreshProject("C") only
    // during A→B null windows. Remembering just the most recently cleared name (then
    // A) makes curName="C" differ, falsely allowing no-current-project writes. The real
    // predicate is whether stores ever established any real project, regardless of which.
    const store = useProjectsStore.getState();
    store.setCurrentProject("C", makeProject("C-数据"), {}, {}); // Establish C.
    store.setCurrentProject(null, null); // Router cleanup: leave C.
    store.setCurrentProject("A", makeProject("A-数据"), {}, {}); // A persists.
    store.setCurrentProject(null, null); // Router cleanup: leave A, B not yet loaded.

    const dC = createDeferred<GetProjectResult>();
    vi.spyOn(API, "getProject").mockReturnValueOnce(dC.promise);

    const pC = useProjectsStore.getState().refreshProject("C"); // Invoke during second null windows.
    dC.resolve(makeResult("C-迟到数据"));
    const ok = await pC;

    expect(ok).toBe("cancelled");
    expect(useProjectsStore.getState().currentProjectName).toBe(null);

    useProjectsStore.getState().setCurrentProject("B", makeProject("B-数据"), {}, {});
    expect(useProjectsStore.getState().currentProjectName).toBe("B");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("B-数据");
  });

  it("项目已切到 B 后才发起的 A 刷新：不写回 store（现役域未 abort，靠当前项目名拦截）", async () => {
    // Unlike preceding cases, switches complete before refreshProject("A") calls: for example postwrite
    // callbacks capture old names but invoke when projects already equal B. They receive B's
    // active un-aborted domains, bypassing signal.aborted/queue deduplication; current-name checks must block them.
    useProjectsStore.getState().setCurrentProject("A", makeProject("A-数据"), {}, {});
    useProjectsStore.getState().setCurrentProject("B", makeProject("B-数据"), {}, {});

    const dA = createDeferred<GetProjectResult>();
    vi.spyOn(API, "getProject").mockReturnValueOnce(dA.promise);

    const pA = useProjectsStore.getState().refreshProject("A");
    dA.resolve(makeResult("A-迟到数据"));
    const ok = await pA;

    expect(ok).toBe("cancelled");
    expect(useProjectsStore.getState().currentProjectName).toBe("B");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("B-数据");
  });

  it("A 在途时排队同项目再刷一轮，期间切到 B：排队轮响应不写回（同名不豁免当前项目核对）", async () => {
    // Codex scenario: initial A in flight plus merged queued A refresh (queuedName === curName,
    // no supersededByOtherProject); users then switch to B. Domain rotation aborts initial rounds,
    // but queued while loops continue using B's active un-aborted domains for queuedName=A
    // second requests. Current projects equal B on settlement; current-name checks, never same-name checks, must block.
    useProjectsStore.getState().setCurrentProject("A", makeProject("A-旧"), {}, {});
    const d1 = createDeferred<GetProjectResult>();
    const d2 = createDeferred<GetProjectResult>();
    vi.spyOn(API, "getProject").mockReturnValueOnce(d1.promise).mockReturnValueOnce(d2.promise);

    const store = useProjectsStore.getState();
    const p1 = store.refreshProject("A");
    const p2 = store.refreshProject("A"); // In flight → merged queue.

    store.setCurrentProject("B", makeProject("B-数据"), {}, {});
    d1.resolve(makeResult("A-首轮迟到"));
    await flush();
    // Initial rounds aborted; queued rounds started second requests.
    d2.resolve(makeResult("A-排队轮迟到"));

    const [ok1, ok2] = await Promise.all([p1, p2]);
    expect(ok1).toBe("cancelled");
    expect(ok2).toBe("cancelled");
    expect(useProjectsStore.getState().currentProjectName).toBe("B");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("B-数据");
  });

  it("项目切换 abort 在途请求：请求被取消，且不按刷新失败提示", async () => {
    useProjectsStore.getState().setCurrentProject("A", makeProject("A-数据"), {}, {});
    let abortedSignal: AbortSignal | undefined;
    vi.spyOn(API, "getProject").mockImplementation(
      (_name, options) =>
        new Promise<GetProjectResult>((_resolve, reject) => {
          abortedSignal = options?.signal;
          options?.signal?.addEventListener("abort", () => {
            reject(new DOMException("Aborted", "AbortError"));
          });
        }),
    );

    const onError = vi.fn();
    const store = useProjectsStore.getState();
    const pA = store.refreshProject("A", { onError });

    store.setCurrentProject("B", makeProject("B-数据"), {}, {});

    const ok = await pA;
    expect(abortedSignal?.aborted).toBe(true);
    expect(ok).toBe("cancelled");
    // Aborts normally follow project switches, never refresh failure; show no project-sync failures.
    expect(onError).not.toHaveBeenCalled();
    expect(useProjectsStore.getState().currentProjectName).toBe("B");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("B-数据");
  });

  it("切换项目后新项目的刷新照常生效（取消域轮换不会长期作废后续刷新）", async () => {
    useProjectsStore.getState().setCurrentProject("A", makeProject("A-数据"), {}, {});
    const dA = createDeferred<GetProjectResult>();
    vi.spyOn(API, "getProject")
      .mockReturnValueOnce(dA.promise)
      .mockResolvedValue(makeResult("B-新数据"));

    const store = useProjectsStore.getState();
    const pA = store.refreshProject("A");

    store.setCurrentProject("B", makeProject("B-数据"), {}, {});
    dA.resolve(makeResult("A-迟到数据"));
    expect(await pA).toBe("cancelled");

    const okB = await useProjectsStore.getState().refreshProject("B");
    expect(okB).toBe("success");
    expect(useProjectsStore.getState().currentProjectName).toBe("B");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("B-新数据");
  });

  it("在途轮是当前项目、排队的却是旧项目名：在途轮照常写入，不为排队者让位", async () => {
    // After incorporating initial loads, other queued projects no longer imply this round's replacement: route switches
    // persist names before refresh, making real successors already current. Old-name callers
    // (postwrite refreshes, SSE) can queue without current ownership changes. Yielding would discard current
    // initial data with nobody left to fetch it.
    useProjectsStore.getState().setCurrentProject("B", null);
    const dB = createDeferred<GetProjectResult>();
    const dStale = createDeferred<GetProjectResult>();
    vi.spyOn(API, "getProject").mockImplementation((name) =>
      name === "B" ? dB.promise : dStale.promise,
    );

    const store = useProjectsStore.getState();
    const pB = store.refreshProject("B"); // Current B initial round, in flight.
    const pStale = store.refreshProject("A"); // Old-name callers queue.

    dB.resolve(makeResult("B-数据"));
    expect(await pB).toBe("success");
    expect(useProjectsStore.getState().currentProjectName).toBe("B");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("B-数据");

    // Queued rounds use old A, cannot write because noncurrent, and simply settle cancelled.
    dStale.resolve(makeResult("A-迟到数据"));
    expect(await pStale).toBe("cancelled");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("B-数据");
  });

  it("当前项目的刷新在排队时，持旧项目名的调用方不夺走唯一的排队名额", async () => {
    // Single queued slots retain latest names. With initial loads, displaced requests may own current-project
    // initial refreshes; incoming old-name requests cannot write stores, leaving current data
    // never fetched and projectDetailLoading stuck forever.
    useProjectsStore.getState().setCurrentProject("A", makeProject("A-数据"), {}, {});
    const dSseA = createDeferred<GetProjectResult>();
    const dB = createDeferred<GetProjectResult>();
    vi.spyOn(API, "getProject").mockImplementation((name) =>
      name === "B" ? dB.promise : dSseA.promise,
    );

    const store = useProjectsStore.getState();
    const pSseA = store.refreshProject("A"); // A-page SSE refresh, in flight.
    store.setCurrentProject("B", null); // Route to B: persist names, rotating cancellation domains.
    const pFirstScreenB = store.refreshProject("B"); // B initial refresh → queue.
    const pStaleA = store.refreshProject("A"); // Old-name callers must not displace B.

    dSseA.resolve(makeResult("A-迟到数据"));
    await flush();
    dB.resolve(makeResult("B-数据"));

    expect(await pFirstScreenB).toBe("success");
    expect(useProjectsStore.getState().currentProjectName).toBe("B");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("B-数据");
    expect(await pSseA).toBe("cancelled");
    expect(await pStaleA).toBe("cancelled");
  });
});
