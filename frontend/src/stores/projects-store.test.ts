import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { ApiRequestError } from "@/api/errors";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { episodeScriptRevisionKey, useProjectsStore } from "@/stores/projects-store";
import { DEMO_PROJECT_NAME } from "@/onboarding/demo-project";
import type { ProjectData } from "@/types";
import { createDeferred } from "@/test/deferred";
import { makeWorkspaceResponse } from "@/test/factories";

type GetWorkspaceResult = Awaited<ReturnType<typeof API.getWorkspace>>;

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

function makeResult(title: string, fingerprints: Record<string, number> = {}): GetWorkspaceResult {
  return makeWorkspaceResponse(makeProject(title), fingerprints);
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe("projects-store refreshProject", () => {
  beforeEach(() => {
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useAppStore.setState(useAppStore.getInitialState(), true);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("merges an episode projection and falls back once when the ledger grows or the selection disappears", async () => {
    const project: ProjectData = { ...makeProject("Demo"), episodes: [1, 2].map((episode) => ({
      episode, title: String(episode), script_file: `scripts/episode_${episode}.json`,
      status: "scripted", script_status: "generated",
    })) };
    const full = makeWorkspaceResponse(project, { "old.png": 1 });
    const partial = makeWorkspaceResponse(project, { "new.png": 2 });
    partial.summary.episodes_summary = null;
    partial.summary.episodes = [{ ...partial.summary.episodes[0], status: "completed" }];
    const fetch = vi.spyOn(API, "getWorkspace").mockResolvedValueOnce(full).mockResolvedValueOnce(partial);
    const scope = { episodes: [1] };
    const store = useProjectsStore.getState();
    expect(await store.refreshProject("demo", { scope })).toBe("success");
    expect(fetch.mock.calls[0][1]).toEqual({ signal: expect.any(AbortSignal) });
    expect(await store.refreshProject("demo", { scope })).toBe("success");
    expect(fetch.mock.calls[1][1]).toEqual({ episodes: [1], signal: expect.any(AbortSignal) });
    const current = useProjectsStore.getState();
    expect(current.currentProjectData?.status?.episodes_summary).toEqual({ total: 2, scripted: 2, in_production: 0, completed: 1 });
    expect(current.currentProjectData?.episodes[1].status).toBe("scripted");
    expect(current.assetFingerprints).toEqual({ "old.png": 1, "new.png": 2 });
    const grown = { ...project, episodes: [...project.episodes, { ...project.episodes[0], episode: 3 }] };
    fetch.mockResolvedValueOnce({ ...partial, project: grown }).mockResolvedValueOnce(makeWorkspaceResponse(grown));
    expect(await store.refreshProject("demo", { scope })).toBe("success");
    expect(fetch.mock.calls[3][1]).toEqual({ signal: expect.any(AbortSignal) });
    expect(useProjectsStore.getState().currentProjectData?.episodes).toHaveLength(3);
    fetch.mockRejectedValueOnce(new ApiRequestError("invalid", undefined, 400)).mockResolvedValueOnce(full);
    expect(await store.refreshProject("demo", { scope })).toBe("success");
    expect(fetch.mock.calls[5][1]).toEqual({ signal: expect.any(AbortSignal) });
  });

  it("widens the committed scope when the requested episode is absent from the local ledger", async () => {
    const store = useProjectsStore.getState();
    const response = makeWorkspaceResponse({
      ...makeProject("Demo"),
      episodes: [{ episode: 1, title: "EP1", script_file: "scripts/one.json", script_status: "generated" }],
    });
    const workspace = vi.spyOn(API, "getWorkspace").mockResolvedValue(response);
    expect(await store.refreshProject("demo")).toBe("success");
    const script = vi.spyOn(API, "getScript").mockResolvedValue({
      script: { episode: 1, segments: [] } as never, revision: "r1",
    });
    await store.loadEpisodeScript("demo", 1, "one.json");
    const revision = useProjectsStore.getState().projectWideRevisions.demo;
    expect(await store.refreshProject("demo", { scope: { episodes: [2] } })).toBe("success");
    expect(workspace).toHaveBeenLastCalledWith("demo", { signal: expect.any(AbortSignal) });
    expect(useProjectsStore.getState().projectWideRevisions.demo).toBe(revision + 1);
    expect(script).toHaveBeenCalledTimes(2);
  });

  it("空 name 直接返回 cancelled，不发请求", async () => {
    const spy = vi.spyOn(API, "getWorkspace");
    const result = await useProjectsStore.getState().refreshProject("");
    expect(result).toBe("cancelled");
    expect(spy).not.toHaveBeenCalled();
  });

  it("成功时写入 currentProject 并返回 success", async () => {
    vi.spyOn(API, "getWorkspace").mockResolvedValue(makeResult("Demo", { "a.png": 1 }));
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
    vi.spyOn(API, "getWorkspace").mockResolvedValue(makeResult("Demo"));
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
    vi.spyOn(API, "getWorkspace").mockRejectedValue(err);
    const onError = vi.fn();
    const result = await useProjectsStore.getState().refreshProject("demo", { onError });
    expect(result).toBe("failed");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("旧");
    expect(onError).toHaveBeenCalledWith(err);
  });

  it("在途合并：在途期间的多次请求只多触发一次 getProject，最终反映最新一轮", async () => {
    const d1 = createDeferred<GetWorkspaceResult>();
    const d2 = createDeferred<GetWorkspaceResult>();
    const spy = vi
      .spyOn(API, "getWorkspace")
      .mockReturnValueOnce(d1.promise)
      .mockReturnValueOnce(d2.promise);

    const store = useProjectsStore.getState();
    const p1 = store.refreshProject("demo");
    const p2 = store.refreshProject("demo");
    const p3 = store.refreshProject("demo");
    expect(spy).toHaveBeenCalledTimes(1);

    d1.resolve(makeResult("R1"));
    await flush();
    expect(spy).toHaveBeenCalledTimes(2);

    d2.resolve(makeResult("R2"));
    const [r1, r2, r3] = await Promise.all([p1, p2, p3]);
    expect([r1, r2, r3]).toEqual(["success", "success", "success"]);
    expect(spy).toHaveBeenCalledTimes(2);
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("R2");
  });

  it("首轮失败、排队轮成功时用新值替换旧值；各调用方返回自己那一轮的结果", async () => {
    useProjectsStore.getState().setCurrentProject("demo", makeProject("旧"), {}, {});
    const d1 = createDeferred<GetWorkspaceResult>();
    const d2 = createDeferred<GetWorkspaceResult>();
    vi.spyOn(API, "getWorkspace").mockReturnValueOnce(d1.promise).mockReturnValueOnce(d2.promise);

    const store = useProjectsStore.getState();
    const p1 = store.refreshProject("demo");
    const p2 = store.refreshProject("demo");

    d1.reject(new Error("first fail"));
    await flush();
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("旧");

    d2.resolve(makeResult("新"));
    const [r1, r2] = await Promise.all([p1, p2]);
    expect(r1).toBe("failed");
    expect(r2).toBe("success");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("新");
  });

  it("首轮成功、排队轮失败时，首轮调用方仍返回 success（不被无关的后续轮次拖累）", async () => {
    const d1 = createDeferred<GetWorkspaceResult>();
    const d2 = createDeferred<GetWorkspaceResult>();
    vi.spyOn(API, "getWorkspace").mockReturnValueOnce(d1.promise).mockReturnValueOnce(d2.promise);

    const store = useProjectsStore.getState();
    const pMoveShot = store.refreshProject("demo");
    const pSse = store.refreshProject("demo");

    d1.resolve(makeResult("重排后"));
    await flush();
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("重排后");

    d2.reject(new Error("sse round fail"));
    const [okMoveShot, okSse] = await Promise.all([pMoveShot, pSse]);
    expect(okMoveShot).toBe("success");
    expect(okSse).toBe("failed");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("重排后");
  });

  it("排队期间被更晚的不同项目请求取代：被取代的调用方立即收到 cancelled，不与新项目的结果混同", async () => {
    const dA = createDeferred<GetWorkspaceResult>();
    const dC = createDeferred<GetWorkspaceResult>();
    vi.spyOn(API, "getWorkspace").mockReturnValueOnce(dA.promise).mockReturnValueOnce(dC.promise);

    const store = useProjectsStore.getState();
    const pA = store.refreshProject("A");
    const pB = store.refreshProject("B");
    const pC = store.refreshProject("C");

    const okB = await pB;
    expect(okB).toBe("cancelled");
    expect(API.getWorkspace).toHaveBeenCalledTimes(1);

    dA.resolve(makeResult("A-数据"));
    await flush();
    expect(useProjectsStore.getState().currentProjectData?.title).not.toBe("A-数据");
    expect(API.getWorkspace).toHaveBeenCalledTimes(2);

    dC.resolve(makeResult("C-数据"));
    const [okA, okC] = await Promise.all([pA, pC]);
    expect(okA).toBe("cancelled");
    expect(okC).toBe("success");
    expect(useProjectsStore.getState().currentProjectName).toBe("C");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("C-数据");
  });

  it("跨项目合并：A 在途时排队刷新 B，A 的响应不写入 store（避免覆盖排队中的 B）", async () => {
    useProjectsStore.getState().setCurrentProject("B", makeProject("B-旧"), {}, {});
    const dA = createDeferred<GetWorkspaceResult>();
    const dB = createDeferred<GetWorkspaceResult>();
    vi.spyOn(API, "getWorkspace").mockReturnValueOnce(dA.promise).mockReturnValueOnce(dB.promise);

    const store = useProjectsStore.getState();
    const pA = store.refreshProject("A");
    const pB = store.refreshProject("B");

    dA.resolve(makeResult("A-数据"));
    await flush();
    expect(useProjectsStore.getState().currentProjectName).toBe("B");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("B-旧");

    dB.reject(new Error("B failed"));
    const [okA, okB] = await Promise.all([pA, pB]);
    expect(okA).toBe("cancelled");
    expect(okB).toBe("failed");
    expect(useProjectsStore.getState().currentProjectName).toBe("B");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("B-旧");
  });

  it("排队轮 onError：首轮无回调、排队轮有回调且失败时通知排队轮回调", async () => {
    const d1 = createDeferred<GetWorkspaceResult>();
    const d2 = createDeferred<GetWorkspaceResult>();
    vi.spyOn(API, "getWorkspace").mockReturnValueOnce(d1.promise).mockReturnValueOnce(d2.promise);
    const onError2 = vi.fn();

    const store = useProjectsStore.getState();
    const p1 = store.refreshProject("demo");
    const p2 = store.refreshProject("demo", { onError: onError2 });

    d1.resolve(makeResult("R1"));
    await flush();
    const err = new Error("round2 fail");
    d2.reject(err);
    await Promise.all([p1, p2]);
    expect(onError2).toHaveBeenCalledWith(err);
  });

  it("合并期间累积 invalidateKeys：排队轮成功后一并失效", async () => {
    const d1 = createDeferred<GetWorkspaceResult>();
    const d2 = createDeferred<GetWorkspaceResult>();
    vi.spyOn(API, "getWorkspace").mockReturnValueOnce(d1.promise).mockReturnValueOnce(d2.promise);

    const store = useProjectsStore.getState();
    const p1 = store.refreshProject("demo", { invalidateKeys: ["segment:S1"] });
    const p2 = store.refreshProject("demo", { invalidateKeys: ["segment:S2"] });

    d1.resolve(makeResult("R1"));
    await flush();
    d2.resolve(makeResult("R2"));
    await Promise.all([p1, p2]);

    const app = useAppStore.getState();
    expect(app.getEntityRevision("segment:S1")).toBe(1);
    expect(app.getEntityRevision("segment:S2")).toBe(1);
  });

  it("真实项目的刷新在只读演示项目接管当前视图后落定：不覆盖回真实项目", async () => {
    const d = createDeferred<GetWorkspaceResult>();
    vi.spyOn(API, "getWorkspace").mockReturnValueOnce(d.promise);

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
    useProjectsStore.getState().setCurrentProject("A", makeProject("A-数据"), {}, {});
    const dA = createDeferred<GetWorkspaceResult>();
    vi.spyOn(API, "getWorkspace").mockReturnValueOnce(dA.promise);

    const store = useProjectsStore.getState();
    const pA = store.refreshProject("A");

    store.setCurrentProject(null, null);
    store.setCurrentProject("B", makeProject("B-数据"), {}, {});

    dA.resolve(makeResult("A-迟到数据"));
    const ok = await pA;

    expect(ok).toBe("cancelled");
    expect(useProjectsStore.getState().currentProjectName).toBe("B");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("B-数据");
  });

  it("null 过渡窗口内发起的旧项目刷新：不豁免为「无当前项目」，不写回", async () => {
    useProjectsStore.getState().setCurrentProject("A", makeProject("A-数据"), {}, {});
    useProjectsStore.getState().setCurrentProject(null, null);

    const dA = createDeferred<GetWorkspaceResult>();
    vi.spyOn(API, "getWorkspace").mockReturnValueOnce(dA.promise);

    const pA = useProjectsStore.getState().refreshProject("A");
    dA.resolve(makeResult("A-迟到数据"));
    const ok = await pA;

    expect(ok).toBe("cancelled");
    expect(useProjectsStore.getState().currentProjectName).toBe(null);
    expect(useProjectsStore.getState().currentProjectData).toBe(null);

    useProjectsStore.getState().setCurrentProject("B", makeProject("B-数据"), {}, {});
    expect(useProjectsStore.getState().currentProjectName).toBe("B");
  });

  it("跨两次切换的更早项目迟到写入（C→A→B）：null 窗口不因换了新名字而放行", async () => {
    const store = useProjectsStore.getState();
    store.setCurrentProject("C", makeProject("C-数据"), {}, {});
    store.setCurrentProject(null, null);
    store.setCurrentProject("A", makeProject("A-数据"), {}, {});
    store.setCurrentProject(null, null);

    const dC = createDeferred<GetWorkspaceResult>();
    vi.spyOn(API, "getWorkspace").mockReturnValueOnce(dC.promise);

    const pC = useProjectsStore.getState().refreshProject("C");
    dC.resolve(makeResult("C-迟到数据"));
    const ok = await pC;

    expect(ok).toBe("cancelled");
    expect(useProjectsStore.getState().currentProjectName).toBe(null);

    useProjectsStore.getState().setCurrentProject("B", makeProject("B-数据"), {}, {});
    expect(useProjectsStore.getState().currentProjectName).toBe("B");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("B-数据");
  });

  it("项目已切到 B 后才发起的 A 刷新：不写回 store（现役域未 abort，靠当前项目名拦截）", async () => {
    useProjectsStore.getState().setCurrentProject("A", makeProject("A-数据"), {}, {});
    useProjectsStore.getState().setCurrentProject("B", makeProject("B-数据"), {}, {});

    const dA = createDeferred<GetWorkspaceResult>();
    vi.spyOn(API, "getWorkspace").mockReturnValueOnce(dA.promise);

    const pA = useProjectsStore.getState().refreshProject("A");
    dA.resolve(makeResult("A-迟到数据"));
    const ok = await pA;

    expect(ok).toBe("cancelled");
    expect(useProjectsStore.getState().currentProjectName).toBe("B");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("B-数据");
  });

  it("A 在途时排队同项目再刷一轮，期间切到 B：排队轮响应不写回（同名不豁免当前项目核对）", async () => {
    useProjectsStore.getState().setCurrentProject("A", makeProject("A-旧"), {}, {});
    const d1 = createDeferred<GetWorkspaceResult>();
    const d2 = createDeferred<GetWorkspaceResult>();
    vi.spyOn(API, "getWorkspace").mockReturnValueOnce(d1.promise).mockReturnValueOnce(d2.promise);

    const store = useProjectsStore.getState();
    const p1 = store.refreshProject("A");
    const p2 = store.refreshProject("A");

    store.setCurrentProject("B", makeProject("B-数据"), {}, {});
    d1.resolve(makeResult("A-首轮迟到"));
    await flush();
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
    vi.spyOn(API, "getWorkspace").mockImplementation(
      (_name, options) =>
        new Promise<GetWorkspaceResult>((_resolve, reject) => {
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
    expect(onError).not.toHaveBeenCalled();
    expect(useProjectsStore.getState().currentProjectName).toBe("B");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("B-数据");
  });

  it("切换项目后新项目的刷新照常生效（取消域轮换不会长期作废后续刷新）", async () => {
    useProjectsStore.getState().setCurrentProject("A", makeProject("A-数据"), {}, {});
    const dA = createDeferred<GetWorkspaceResult>();
    vi.spyOn(API, "getWorkspace")
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
    useProjectsStore.getState().setCurrentProject("B", null);
    const dB = createDeferred<GetWorkspaceResult>();
    const dStale = createDeferred<GetWorkspaceResult>();
    vi.spyOn(API, "getWorkspace").mockImplementation((name) =>
      name === "B" ? dB.promise : dStale.promise,
    );

    const store = useProjectsStore.getState();
    const pB = store.refreshProject("B");
    const pStale = store.refreshProject("A");

    dB.resolve(makeResult("B-数据"));
    expect(await pB).toBe("success");
    expect(useProjectsStore.getState().currentProjectName).toBe("B");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("B-数据");

    dStale.resolve(makeResult("A-迟到数据"));
    expect(await pStale).toBe("cancelled");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("B-数据");
  });

  it("当前项目的刷新在排队时，持旧项目名的调用方不夺走唯一的排队名额", async () => {
    useProjectsStore.getState().setCurrentProject("A", makeProject("A-数据"), {}, {});
    const dSseA = createDeferred<GetWorkspaceResult>();
    const dB = createDeferred<GetWorkspaceResult>();
    vi.spyOn(API, "getWorkspace").mockImplementation((name) =>
      name === "B" ? dB.promise : dSseA.promise,
    );

    const store = useProjectsStore.getState();
    const pSseA = store.refreshProject("A");
    store.setCurrentProject("B", null);
    const pFirstScreenB = store.refreshProject("B");
    const pStaleA = store.refreshProject("A");

    dSseA.resolve(makeResult("A-迟到数据"));
    await flush();
    dB.resolve(makeResult("B-数据"));

    expect(await pFirstScreenB).toBe("success");
    expect(useProjectsStore.getState().currentProjectName).toBe("B");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("B-数据");
    expect(await pSseA).toBe("cancelled");
    expect(await pStaleA).toBe("cancelled");
  });

  it.each([new Error("script reload failed"), new ApiRequestError("script missing", undefined, 404)])(
    "活动剧本重读失败时写后刷新返回失败，并通知调用方（%s）", async (failure) => {
      const project = makeWorkspaceResponse({
        ...makeProject("Demo"),
        episodes: [{ episode: 1, title: "EP1", script_file: "scripts/episode_1.json", script_status: "generated" }],
      });
      const store = useProjectsStore.getState();
      store.setCurrentProject("demo", project.project);
      vi.spyOn(API, "getWorkspace").mockResolvedValue(project);
      vi.spyOn(API, "getScript")
        .mockResolvedValueOnce({ script: { episode: 1, segments: [] } as never, revision: "old" })
        .mockRejectedValueOnce(failure)
        .mockResolvedValueOnce({ script: { episode: 1, segments: [] } as never, revision: "new" });
      expect(await store.loadEpisodeScript("demo", 1, "episode_1.json")).toBe("success");

      const onError = vi.fn();
      expect(await store.refreshProject("demo", { onError })).toBe("failed");
      expect(onError).toHaveBeenCalledWith(failure);
      expect(useProjectsStore.getState().activeEpisodeScript.status).toBe("error");
      expect(useProjectsStore.getState().currentScripts).toEqual({});
      expect(await store.refreshProject("demo")).toBe("success");
      expect(useProjectsStore.getState().activeEpisodeScript.revision).toBe("new");
    },
  );

  it("概览取消活动剧本时保留取消结果，并向 SSE 报告工作台已提交", async () => {
    const project = makeWorkspaceResponse({
      ...makeProject("Demo"),
      episodes: [{ episode: 1, title: "EP1", script_file: "scripts/custom.json", script_status: "generated" }],
    });
    const store = useProjectsStore.getState();
    store.setCurrentProject("demo", project.project);
    const snapshot = { script: { episode: 1, segments: [] } as never, revision: "r1" };
    const reread = createDeferred<typeof snapshot>();
    const started = createDeferred<void>();
    vi.spyOn(API, "getWorkspace").mockResolvedValue(project);
    vi.spyOn(API, "getScript").mockResolvedValueOnce(snapshot).mockImplementationOnce(() => {
      started.resolve();
      return reread.promise;
    });
    await store.loadEpisodeScript("demo", 1, "custom.json");
    const committed = vi.fn();
    const refreshed = store.refreshProject("demo", { onScriptCancelledAfterCommit: committed });
    await started.promise;
    store.clearActiveEpisodeScript();
    reread.resolve(snapshot);
    expect(await refreshed).toBe("cancelled");
    expect(committed).toHaveBeenCalledOnce();
  });

  it("账本确认同一绑定后路由复用先到的可选读取空结果", async () => {
    const store = useProjectsStore.getState();
    store.setCurrentProject("demo", null);
    const script = vi.spyOn(API, "getScript")
      .mockResolvedValueOnce(null)
      .mockRejectedValueOnce(new ApiRequestError("unavailable", undefined, 503));
    vi.spyOn(API, "getWorkspace").mockResolvedValue(makeWorkspaceResponse({
      ...makeProject("Demo"),
      episodes: [{ episode: 1, title: "EP1", script_file: "scripts/episode_1.json", script_status: "generated" }],
    }));
    await store.loadEpisodeScript("demo", 1, "episode_1.json", { speculative: true });
    expect(await store.refreshProject("demo")).toBe("success");
    expect(await store.loadEpisodeScript("demo", 1, "episode_1.json")).toBe("success");
    expect(script).toHaveBeenCalledTimes(1);
    expect(useProjectsStore.getState().activeEpisodeScript.status).toBe("missing");
  });

  it.each(["unchanged", null])("第一份订阅快照与正文版本一致时只读取一次（%s）", async (version) => {
    const store = useProjectsStore.getState();
    store.setCurrentProject("demo", { ...makeProject("Demo"), episodes: [
      { episode: 1, title: "EP1", script_file: "scripts/episode_1.json", script_status: "generated" },
    ] });
    const snapshot = { script: { episode: 1, segments: [] } as never, revision: "r1" };
    const read = vi.spyOn(API, "getScript").mockImplementation(async (_p, _file, options) => {
      options?.onVersion?.(version);
      return version ? snapshot : null;
    });
    store.receiveScriptBaseline("demo", { episode: 1, filename: "episode_1.json", file_version: version });
    await store.loadEpisodeScript("demo", 1, "episode_1.json", { speculative: true });
    expect(read).toHaveBeenCalledTimes(1);
    expect(useProjectsStore.getState().activeEpisodeScript.status).toBe(version ? "ready" : "missing");
  });

  it("订阅迟到时以单文件 stat 降级；重连基线不同会撤回旧正文", async () => {
    vi.useFakeTimers();
    try {
      const store = useProjectsStore.getState();
      const project: ProjectData = { ...makeProject("Demo"), episodes: [
        { episode: 1, title: "EP1", script_file: "scripts/episode_1.json", script_status: "generated" },
      ] };
      store.setCurrentProject("demo", null);
      const old = { script: { episode: 1, segments: [] } as never, revision: "old" };
      vi.spyOn(API, "getScript").mockImplementationOnce(async (_p, _f, options) => {
        options?.onVersion?.("one");
        return old;
      }).mockResolvedValue({ ...old, revision: "new" });
      vi.spyOn(API, "getWorkspace").mockResolvedValue(makeWorkspaceResponse(project));
      const stat = vi.spyOn(API, "getScriptVersion").mockResolvedValue({ file_version: "one" });
      const reading = store.loadEpisodeScript("demo", 1, "episode_1.json", { speculative: true });
      await vi.advanceTimersByTimeAsync(2000);
      expect(stat).not.toHaveBeenCalled();
      expect(await store.refreshProject("demo")).toBe("success");
      await reading;
      expect(stat).toHaveBeenCalledTimes(1);
      expect(useProjectsStore.getState().activeEpisodeScript.revision).toBe("old");
      vi.mocked(API.getWorkspace).mockRejectedValueOnce(new Error("503"));
      store.receiveScriptBaseline("demo", { episode: 1, filename: "episode_1.json", file_version: "two" });
      expect(useProjectsStore.getState().currentScripts).toEqual({});
      await vi.advanceTimersByTimeAsync(0);
      expect(useProjectsStore.getState().activeEpisodeScript).toMatchObject({ status: "error", error: "503" });
      expect(useProjectsStore.getState().currentScripts).toEqual({});
      expect(await store.refreshProject("demo", { scope: { episodes: [1] } })).toBe("success");
      expect(useProjectsStore.getState().activeEpisodeScript.revision).toBe("new");
    } finally {
      vi.useRealTimers();
    }
  });

  it("版本查询失败时用同一绑定已到达的首快照结算推测读取", async () => {
    vi.useFakeTimers();
    try {
      const store = useProjectsStore.getState();
      store.setCurrentProject("demo", { ...makeProject("Demo"), episodes: [
        { episode: 1, title: "EP1", script_file: "scripts/episode_1.json", script_status: "generated" },
      ] });
      const read = vi.spyOn(API, "getScript").mockImplementation(async (_project, _file, options) => {
        options?.onVersion?.("one");
        return { script: { segments: [] } as never, revision: "r1" };
      });
      const probe = createDeferred<{ file_version: string }>();
      vi.spyOn(API, "getScriptVersion").mockReturnValue(probe.promise);
      const reading = store.loadEpisodeScript("demo", 1, "episode_1.json", { speculative: true });
      await vi.advanceTimersByTimeAsync(2000);
      store.receiveScriptBaseline("demo", { episode: 1, filename: "episode_1.json", file_version: "one" });
      probe.reject(new Error("503"));
      expect(await reading).toBe("success");
      expect(useProjectsStore.getState().activeEpisodeScript.revision).toBe("r1");
      expect(read).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it.each(["three", null])("版本探测与已核验正文冲突时，采用补读版本 %s", async (observed) => {
    vi.useFakeTimers();
    try {
      const store = useProjectsStore.getState();
      store.setCurrentProject("demo", { ...makeProject("Demo"), episodes: [
        { episode: 1, title: "EP1", script_file: "scripts/episode_1.json", script_status: "generated" },
      ] });
      const read = vi.spyOn(API, "getScript").mockImplementationOnce(async (_p, _f, options) => {
        options?.onVersion?.("one");
        return { script: { segments: [] } as never, revision: "r1" };
      }).mockImplementationOnce(async (_p, _f, options) => {
        options?.onVersion?.(observed);
        return observed === null ? null : { script: { segments: [] } as never, revision: "r3" };
      });
      const probe = createDeferred<{ file_version: string }>();
      vi.spyOn(API, "getScriptVersion").mockReturnValue(probe.promise);
      const reading = store.loadEpisodeScript("demo", 1, "episode_1.json", { speculative: true });
      await vi.advanceTimersByTimeAsync(2000);
      const binding = { episode: 1, filename: "episode_1.json" };
      store.receiveScriptBaseline("demo", { ...binding, file_version: "one" });
      probe.resolve({ file_version: "two" });
      expect(await reading).toBe("success");
      store.receiveScriptBaseline("demo", { ...binding, file_version: observed });
      expect(useProjectsStore.getState().activeEpisodeScript).toMatchObject({
        status: observed === null ? "missing" : "ready", revision: observed === null ? null : "r3",
      });
      expect(read).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it.each([1, 2])("基线补拉的迟到失败不结束第 %i 集的新读取", async (episode) => {
    const store = useProjectsStore.getState();
    store.setCurrentProject("demo", { ...makeProject("Demo"), episodes: [1, 2].map((id) => ({
      episode: id, title: String(id), script_file: `scripts/episode_${id}.json`, script_status: "generated",
    })) });
    const newer = createDeferred<Awaited<ReturnType<typeof API.getScript>>>();
    vi.spyOn(API, "getScript").mockResolvedValueOnce(null).mockReturnValueOnce(newer.promise);
    await store.loadEpisodeScript("demo", 1, "episode_1.json");
    const repair = createDeferred<GetWorkspaceResult>();
    vi.spyOn(API, "getWorkspace").mockReturnValueOnce(repair.promise);
    store.receiveScriptBaseline("demo", { episode: 1, filename: "episode_1.json", file_version: "created" });
    const reading = store.loadEpisodeScript("demo", episode, `episode_${episode}.json`, { force: true });
    repair.reject(new Error("503"));
    await flush();
    expect(useAppStore.getState().toast).toBeNull();
    expect(useProjectsStore.getState().activeEpisodeScript).toMatchObject({ episode, status: "loading" });
    newer.resolve({ script: { segments: [] } as never, revision: "new" });
    expect(await reading).toBe("success");
    expect(useProjectsStore.getState().activeEpisodeScript.revision).toBe("new");
  });

  it("切换分集后，前一集迟到的首订阅快照不覆盖新集", async () => {
    const store = useProjectsStore.getState();
    store.setCurrentProject("demo", { ...makeProject("Demo"), episodes: [1, 2].map((episode) => ({
      episode, title: String(episode), script_file: `scripts/episode_${episode}.json`, script_status: "generated",
    })) });
    const first = createDeferred<void>();
    const read = vi.spyOn(API, "getScript").mockImplementation(async (_p, file, options) => {
      if (file === "episode_1.json") {
        options?.onVersion?.("A-old");
        first.resolve();
      }
      return { script: { segments: [] } as never, revision: file };
    });
    const a = store.loadEpisodeScript("demo", 1, "episode_1.json", { speculative: true });
    await first.promise;
    await store.loadEpisodeScript("demo", 2, "episode_2.json");
    store.receiveScriptBaseline("demo", { episode: 1, filename: "episode_1.json", file_version: "A-new" });
    expect(await a).toBe("cancelled");
    expect(Object.keys(useProjectsStore.getState().currentScripts)).toEqual(["episode_2.json"]);
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("初始账本失败时结束等待绑定的推测读取", async () => {
    const store = useProjectsStore.getState();
    store.setCurrentProject("demo", null);
    vi.spyOn(API, "getScript").mockResolvedValue({ script: { episode: 1, segments: [] } as never, revision: "r1" });
    vi.spyOn(API, "getWorkspace").mockRejectedValue(new Error("workspace failed"));
    const read = store.loadEpisodeScript("demo", 1, "episode_1.json", { speculative: true });
    expect(await store.refreshProject("demo")).toBe("failed");
    expect(await read).toBe("cancelled");
    expect(useProjectsStore.getState().currentScripts).toEqual({});
  });

  it("按集加载剧本：绑定变更时丢弃旧脚本并拉取新绑定，作用域刷新仅推进对应集修订", async () => {
    const projectV1: ProjectData = {
      ...makeProject("Demo"),
      episodes: [
        { episode: 1, title: "EP1", script_file: "scripts/episode_1.json", script_status: "generated" },
        { episode: 2, title: "EP2", script_file: "scripts/episode_2.json", script_status: "generated" },
      ],
    };
    const projectV2: ProjectData = {
      ...projectV1,
      episodes: [
        { episode: 1, title: "EP1", script_file: "scripts/episode_1_v2.json", script_status: "generated" },
        { episode: 2, title: "EP2", script_file: "scripts/episode_2.json", script_status: "generated" },
      ],
    };
    const partial = makeWorkspaceResponse(projectV2);
    partial.summary.episodes_summary = null;
    partial.summary.episodes = partial.summary.episodes.filter((row) => row.episode === 1);
    const workspace = vi.spyOn(API, "getWorkspace").mockResolvedValueOnce(makeWorkspaceResponse(projectV1))
      .mockResolvedValueOnce(partial);
    expect(await useProjectsStore.getState().refreshProject("demo")).toBe("success");
    const getScriptSpy = vi.spyOn(API, "getScript").mockImplementation((_proj, file) =>
      Promise.resolve({ script: { episode: 1, segments: [], file } as never, revision: `rev:${file}` }),
    );

    expect(await useProjectsStore.getState().loadEpisodeScript("demo", 1, "episode_1.json")).toBe("success");
    expect(Object.keys(useProjectsStore.getState().currentScripts)).toEqual(["episode_1.json"]);

    const beforeWide = useProjectsStore.getState().projectWideRevisions.demo ?? 0;
    const beforeEp2 = useProjectsStore.getState().episodeRevisions[episodeScriptRevisionKey("demo", 2)] ?? 0;
    expect(await useProjectsStore.getState().refreshProject("demo", { scope: { episodes: [1] } })).toBe("success");

    expect(workspace).toHaveBeenLastCalledWith("demo", { episodes: [1], signal: expect.any(AbortSignal) });
    expect(useProjectsStore.getState().projectWideRevisions.demo ?? 0).toBe(beforeWide);
    expect(useProjectsStore.getState().episodeRevisions[episodeScriptRevisionKey("demo", 2)] ?? 0).toBe(beforeEp2);
    expect(useProjectsStore.getState().episodeRevisions[episodeScriptRevisionKey("demo", 1)]).toBe(1);
    await vi.waitFor(() => {
      expect(getScriptSpy).toHaveBeenCalledWith("demo", "episode_1_v2.json", { signal: expect.any(AbortSignal) });
    });
    expect(Object.keys(useProjectsStore.getState().currentScripts)).toEqual(["episode_1_v2.json"]);
  });
});
