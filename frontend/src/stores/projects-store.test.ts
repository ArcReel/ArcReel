import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectData } from "@/types";

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

// 手动可控的 deferred promise，用于把 getProject 卡在「在途」状态精确编排合并时序。
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

// 冲刷 microtask + timer 队列，让在途刷新的续跑推进到下一次 await。
const flush = () => new Promise((r) => setTimeout(r, 0));

describe("projects-store refreshProject", () => {
  beforeEach(() => {
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useAppStore.setState(useAppStore.getInitialState(), true);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("空 name 直接返回 false，不发请求", async () => {
    const spy = vi.spyOn(API, "getProject");
    const ok = await useProjectsStore.getState().refreshProject("");
    expect(ok).toBe(false);
    expect(spy).not.toHaveBeenCalled();
  });

  it("成功时写入 currentProject 并返回 true", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue(makeResult("Demo", { "a.png": 1 }));
    const ok = await useProjectsStore.getState().refreshProject("demo");
    expect(ok).toBe(true);
    const s = useProjectsStore.getState();
    expect(s.currentProjectName).toBe("demo");
    expect(s.currentProjectData?.title).toBe("Demo");
    expect(s.getAssetFingerprint("a.png")).toBe(1);
  });

  it("成功后按 invalidateKeys 失效实体版本", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue(makeResult("Demo"));
    await useProjectsStore
      .getState()
      .refreshProject("demo", { invalidateKeys: ["segment:S1", "character:hero"] });
    const app = useAppStore.getState();
    expect(app.getEntityRevision("segment:S1")).toBe(1);
    expect(app.getEntityRevision("character:hero")).toBe(1);
  });

  it("失败留旧：不覆盖 currentProjectData，返回 false，onError 收到错误", async () => {
    useProjectsStore.getState().setCurrentProject("demo", makeProject("旧"), {}, {});
    const err = new Error("boom");
    vi.spyOn(API, "getProject").mockRejectedValue(err);
    const onError = vi.fn();
    const ok = await useProjectsStore.getState().refreshProject("demo", { onError });
    expect(ok).toBe(false);
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("旧");
    expect(onError).toHaveBeenCalledWith(err);
  });

  it("在途合并：在途期间的多次请求只多触发一次 getProject，最终反映最新一轮", async () => {
    const d1 = deferred<GetProjectResult>();
    const d2 = deferred<GetProjectResult>();
    const spy = vi
      .spyOn(API, "getProject")
      .mockReturnValueOnce(d1.promise)
      .mockReturnValueOnce(d2.promise);

    const store = useProjectsStore.getState();
    const p1 = store.refreshProject("demo"); // owner：发起第一轮
    const p2 = store.refreshProject("demo"); // 在途 → 合并
    const p3 = store.refreshProject("demo"); // 在途 → 合并
    // 合并期间只发起了第一轮请求
    expect(spy).toHaveBeenCalledTimes(1);

    d1.resolve(makeResult("R1"));
    await flush();
    // 排队请求收敛为「结束后再跑一轮」，此刻第二轮已发起
    expect(spy).toHaveBeenCalledTimes(2);

    d2.resolve(makeResult("R2"));
    const [r1, r2, r3] = await Promise.all([p1, p2, p3]);
    expect([r1, r2, r3]).toEqual([true, true, true]);
    // 3 个刷新意图合并为 2 次请求，store 落定在最新一轮
    expect(spy).toHaveBeenCalledTimes(2);
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("R2");
  });

  it("首轮失败、排队轮成功时用新值替换旧值；各调用方返回自己那一轮的结果", async () => {
    useProjectsStore.getState().setCurrentProject("demo", makeProject("旧"), {}, {});
    const d1 = deferred<GetProjectResult>();
    const d2 = deferred<GetProjectResult>();
    vi.spyOn(API, "getProject").mockReturnValueOnce(d1.promise).mockReturnValueOnce(d2.promise);

    const store = useProjectsStore.getState();
    const p1 = store.refreshProject("demo");
    const p2 = store.refreshProject("demo"); // 合并 → 结束后再跑一轮

    d1.reject(new Error("first fail"));
    await flush();
    // 第一轮失败：留旧
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("旧");

    d2.resolve(makeResult("新"));
    const [r1, r2] = await Promise.all([p1, p2]);
    // 首轮调用方拿到自己那一轮的真实结果（失败），不因排队轮后续成功被覆盖；
    // 排队轮调用方拿到自己那一轮的结果（成功）。
    expect(r1).toBe(false);
    expect(r2).toBe(true);
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("新");
  });

  it("首轮成功、排队轮失败时，首轮调用方仍返回 true（不被无关的后续轮次拖累）", async () => {
    const d1 = deferred<GetProjectResult>();
    const d2 = deferred<GetProjectResult>();
    vi.spyOn(API, "getProject").mockReturnValueOnce(d1.promise).mockReturnValueOnce(d2.promise);

    const store = useProjectsStore.getState();
    // 例如 handleMoveShot：重排后发起刷新，依赖返回值推进选中态。
    const pMoveShot = store.refreshProject("demo");
    // 合并期间到达的另一意图（如 SSE 刷新），随后失败。
    const pSse = store.refreshProject("demo");

    d1.resolve(makeResult("重排后"));
    await flush();
    // 首轮已成功写入
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("重排后");

    d2.reject(new Error("sse round fail"));
    const [okMoveShot, okSse] = await Promise.all([pMoveShot, pSse]);
    // 首轮调用方拿到自己那一轮的真实结果（成功），不因排队轮后续失败被覆盖为 false。
    expect(okMoveShot).toBe(true);
    expect(okSse).toBe(false);
    // 失败留旧：store 仍保留首轮写入的数据，不因排队轮失败被清空或回滚。
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("重排后");
  });

  it("排队期间被更晚的不同项目请求取代：被取代的调用方立即收到 false，不与新项目的结果混同", async () => {
    const dA = deferred<GetProjectResult>();
    const dC = deferred<GetProjectResult>();
    vi.spyOn(API, "getProject").mockReturnValueOnce(dA.promise).mockReturnValueOnce(dC.promise);

    const store = useProjectsStore.getState();
    const pA = store.refreshProject("A"); // 发起中
    const pB = store.refreshProject("B"); // 排队 → queuedName = B
    const pC = store.refreshProject("C"); // 排队期间到达不同项目：B 被 C 取代

    // B 的调用方无需等 A / C 落定，在被取代的一刻就立即收到 false——
    // 它请求的项目从未被真正拉取过，不能被并入 C 的结果。
    const okB = await pB;
    expect(okB).toBe(false);
    // 只发起了 A 的请求；B 被取代时尚未轮到它，不产生任何请求。
    expect(API.getProject).toHaveBeenCalledTimes(1);

    dA.resolve(makeResult("A-数据"));
    await flush();
    // A 的响应到达，但排队目标已是 C：不提交（既有行为），且已发起 C 的请求。
    expect(useProjectsStore.getState().currentProjectData?.title).not.toBe("A-数据");
    expect(API.getProject).toHaveBeenCalledTimes(2);

    dC.resolve(makeResult("C-数据"));
    const [okA, okC] = await Promise.all([pA, pC]);
    expect(okA).toBe(false);
    expect(okC).toBe(true);
    expect(useProjectsStore.getState().currentProjectName).toBe("C");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("C-数据");
  });

  it("跨项目合并：A 在途时排队刷新 B，A 的响应不写入 store（避免覆盖排队中的 B）", async () => {
    useProjectsStore.getState().setCurrentProject("B", makeProject("B-旧"), {}, {});
    const dA = deferred<GetProjectResult>();
    const dB = deferred<GetProjectResult>();
    vi.spyOn(API, "getProject").mockReturnValueOnce(dA.promise).mockReturnValueOnce(dB.promise);

    const store = useProjectsStore.getState();
    const pA = store.refreshProject("A");
    const pB = store.refreshProject("B"); // 合并 → 排队到不同名称

    dA.resolve(makeResult("A-数据"));
    await flush();
    // A 的响应到达，但排队目标已是不同项目 B：不提交，store 仍是 B 的旧数据
    expect(useProjectsStore.getState().currentProjectName).toBe("B");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("B-旧");

    dB.reject(new Error("B failed"));
    const [okA, okB] = await Promise.all([pA, pB]);
    expect(okA).toBe(false);
    expect(okB).toBe(false);
    // B 轮失败：留旧，仍是 B 的旧数据，绝不能变成 A 的数据
    expect(useProjectsStore.getState().currentProjectName).toBe("B");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("B-旧");
  });

  it("排队轮 onError：首轮无回调、排队轮有回调且失败时通知排队轮回调", async () => {
    const d1 = deferred<GetProjectResult>();
    const d2 = deferred<GetProjectResult>();
    vi.spyOn(API, "getProject").mockReturnValueOnce(d1.promise).mockReturnValueOnce(d2.promise);
    const onError2 = vi.fn();

    const store = useProjectsStore.getState();
    const p1 = store.refreshProject("demo"); // 首轮无 onError
    const p2 = store.refreshProject("demo", { onError: onError2 }); // 排队轮带 onError

    d1.resolve(makeResult("R1"));
    await flush();
    const err = new Error("round2 fail");
    d2.reject(err);
    await Promise.all([p1, p2]);
    expect(onError2).toHaveBeenCalledWith(err);
  });

  it("合并期间累积 invalidateKeys：排队轮成功后一并失效", async () => {
    const d1 = deferred<GetProjectResult>();
    const d2 = deferred<GetProjectResult>();
    vi.spyOn(API, "getProject").mockReturnValueOnce(d1.promise).mockReturnValueOnce(d2.promise);

    const store = useProjectsStore.getState();
    const p1 = store.refreshProject("demo", { invalidateKeys: ["segment:S1"] });
    const p2 = store.refreshProject("demo", { invalidateKeys: ["segment:S2"] });

    d1.resolve(makeResult("R1"));
    await flush();
    d2.resolve(makeResult("R2"));
    await Promise.all([p1, p2]);

    const app = useAppStore.getState();
    // 第一轮失效 S1；排队轮把 S2 带上（S1 不重复计入排队轮）
    expect(app.getEntityRevision("segment:S1")).toBe(1);
    expect(app.getEntityRevision("segment:S2")).toBe(1);
  });
});
