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

  it("首轮失败、排队轮成功时用新值替换旧值并返回 true", async () => {
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
    // 返回最后一轮结果（成功）
    expect([r1, r2]).toEqual([true, true]);
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("新");
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
