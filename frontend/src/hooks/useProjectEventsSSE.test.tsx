import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router, useLocation } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API, type ProjectEventStreamOptions } from "@/api";
import { useProjectEventsSSE } from "./useProjectEventsSSE";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useCostStore } from "@/stores/cost-store";

function HookHarness({ projectName }: { projectName: string }) {
  useProjectEventsSSE(projectName);
  const [location] = useLocation();
  return <div data-testid="location">{location}</div>;
}

function renderHarness(path = "/") {
  const { hook } = memoryLocation({ path });
  return render(
    <Router hook={hook}>
      <HookHarness projectName="demo" />
    </Router>,
  );
}

type GetProjectResult = Awaited<ReturnType<typeof API.getProject>>;

// 手动可控的 deferred promise，用于把 getProject 卡在「在途」状态精确编排两批
// onChanges 的重叠时序（对齐 projects-store.test.ts 的既有模式）。
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function makeGetProjectResult(title: string): GetProjectResult {
  return {
    project: {
      title,
      content_mode: "narration",
      style: "Anime",
      episodes: [],
      characters: { hero: { description: "勇者" } },
      scenes: { 酒馆: { description: "小镇酒馆" } },
      props: {},
    },
    scripts: {},
  };
}

describe("useProjectEventsSSE", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    useAppStore.setState(useAppStore.getInitialState(), true);
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        content_mode: "narration",
        style: "Anime",
        episodes: [{ episode: 1, title: "第一集", script_file: "scripts/episode_1.json" }],
        characters: { hero: { description: "勇者" } },
        scenes: {},
        props: {},
      },
      scripts: {
        "episode_1.json": {
          episode: 1,
          title: "第一集",
          content_mode: "narration",
          duration_seconds: 4,
          novel: { title: "", chapter: "" },
          segments: [],
        },
      },
    });
  });

  it("refreshes and navigates to the focused workspace target for remote changes", async () => {
    let capturedOptions: ProjectEventStreamOptions | undefined;
    vi.spyOn(API, "openProjectEventStream").mockImplementation((options) => {
      capturedOptions = options;
      return { close: vi.fn() } as unknown as EventSource;
    });

    renderHarness("/");
    expect(capturedOptions).toBeDefined();
    expect(capturedOptions?.projectName).toBe("demo");

    act(() => {
      capturedOptions?.onChanges?.(
        {
          project_name: "demo",
          batch_id: "batch-1",
          fingerprint: "fp-1",
          generated_at: "2026-03-01T00:00:00Z",
          source: "filesystem",
          changes: [
            {
              entity_type: "character",
              action: "created",
              entity_id: "hero",
              label: "角色「hero」",
              focus: {
                pane: "characters",
                anchor_type: "character",
                anchor_id: "hero",
              },
              important: true,
            },
          ],
        },
        new MessageEvent("changes"),
      );
    });

    await waitFor(() => {
      expect(API.getProject).toHaveBeenCalledWith("demo");
      expect(screen.getByTestId("location")).toHaveTextContent("/characters");
    });
    expect(useAppStore.getState().scrollTarget).toEqual(
      expect.objectContaining({
        type: "character",
        id: "hero",
        route: "/characters",
      }),
    );
    expect(useAppStore.getState().workspaceNotifications[0]).toEqual(
      expect.objectContaining({
        text: "AI 刚新增了 角色「hero」，点击查看",
        target: expect.objectContaining({
          type: "character",
          id: "hero",
          route: "/characters",
        }),
      }),
    );
    expect(useAppStore.getState().assistantToolActivitySuppressed).toBe(true);
  });

  it("navigates reference video units to the reference canvas via reference_unit target", async () => {
    let capturedOptions: ProjectEventStreamOptions | undefined;
    vi.spyOn(API, "openProjectEventStream").mockImplementation((options) => {
      capturedOptions = options;
      return { close: vi.fn() } as unknown as EventSource;
    });

    renderHarness("/episodes/1");

    act(() => {
      capturedOptions?.onChanges?.(
        {
          project_name: "demo",
          batch_id: "batch-ref",
          fingerprint: "fp-ref",
          generated_at: "2026-03-01T00:00:00Z",
          source: "worker",
          changes: [
            {
              entity_type: "reference_unit",
              action: "created",
              entity_id: "E1U01",
              label: "视频单元「E1U01」",
              episode: 1,
              focus: {
                pane: "episode",
                episode: 1,
                anchor_type: "reference_unit",
                anchor_id: "E1U01",
              },
              important: true,
            },
          ],
        },
        new MessageEvent("changes"),
      );
    });

    await waitFor(() => {
      expect(API.getProject).toHaveBeenCalledWith("demo");
      expect(useAppStore.getState().scrollTarget).toEqual(
        expect.objectContaining({
          type: "reference_unit",
          id: "E1U01",
          route: "/episodes/1",
        }),
      );
    });
    expect(useAppStore.getState().workspaceNotifications[0]).toEqual(
      expect.objectContaining({
        text: "AI 刚新增了 视频单元「E1U01」，点击查看",
        target: expect.objectContaining({
          type: "reference_unit",
          id: "E1U01",
          route: "/episodes/1",
        }),
      }),
    );
  });

  it("defers focus when the user is editing", async () => {
    let capturedOptions: ProjectEventStreamOptions | undefined;
    vi.spyOn(API, "openProjectEventStream").mockImplementation((options) => {
      capturedOptions = options;
      return { close: vi.fn() } as unknown as EventSource;
    });

    renderHarness("/");
    const input = document.createElement("input");
    document.body.appendChild(input);
    input.focus();

    act(() => {
      capturedOptions?.onChanges?.(
        {
          project_name: "demo",
          batch_id: "batch-2",
          fingerprint: "fp-2",
          generated_at: "2026-03-01T00:00:00Z",
          source: "worker",
          changes: [
            {
              entity_type: "scene",
              action: "updated",
              entity_id: "酒馆",
              label: "场景「酒馆」",
              focus: {
                pane: "scenes",
                anchor_type: "scene",
                anchor_id: "酒馆",
              },
              important: true,
            },
          ],
        },
        new MessageEvent("changes"),
      );
    });

    await waitFor(() => {
      expect(API.getProject).toHaveBeenCalledWith("demo");
      expect(useAppStore.getState().workspaceNotifications[0]?.target?.id).toBe("酒馆");
    });
    expect(screen.getByTestId("location")).toHaveTextContent("/");
    expect(useAppStore.getState().scrollTarget).toBeNull();
  });

  it("shows a toast without navigation for generation completion batches", async () => {
    let capturedOptions: ProjectEventStreamOptions | undefined;
    vi.spyOn(API, "openProjectEventStream").mockImplementation((options) => {
      capturedOptions = options;
      return { close: vi.fn() } as unknown as EventSource;
    });

    renderHarness("/episodes/1");

    act(() => {
      capturedOptions?.onChanges?.(
        {
          project_name: "demo",
          batch_id: "batch-3",
          fingerprint: "fp-3",
          generated_at: "2026-03-01T00:00:00Z",
          source: "worker",
          changes: [
            {
              entity_type: "segment",
              action: "storyboard_ready",
              entity_id: "E1S01",
              label: "分镜「E1S01」",
              episode: 1,
              focus: null,
              important: true,
            },
          ],
        },
        new MessageEvent("changes"),
      );
    });

    await waitFor(() => {
      expect(API.getProject).toHaveBeenCalledWith("demo");
      expect(useAppStore.getState().toast?.text).toBe("分镜「E1S01」的分镜图已生成");
    });
    expect(useAppStore.getState().toast?.tone).toBe("success");
    expect(useAppStore.getState().workspaceNotifications[0]).toEqual(
      expect.objectContaining({
        text: "分镜「E1S01」的分镜图已生成",
        tone: "success",
        target: null,
      }),
    );
    expect(screen.getByTestId("location")).toHaveTextContent("/episodes/1");
    expect(useAppStore.getState().scrollTarget).toBeNull();
  });

  it.each([
    {
      action: "grid_ready" as const,
      entityType: "grid" as const,
      entityId: "G01",
      label: "宫格「G01」",
      expectedText: "宫格「G01」已生成",
    },
    {
      action: "reference_video_ready" as const,
      entityType: "reference_unit" as const,
      entityId: "U01",
      label: "参考视频「U01」",
      expectedText: "参考视频「U01」已生成",
    },
    {
      action: "tts_ready" as const,
      entityType: "segment" as const,
      entityId: "E1S01",
      label: "旁白「E1S01」",
      expectedText: "旁白「E1S01」已生成",
    },
  ])(
    "shows a generation-completed toast and refreshes cost for $action, without navigation",
    async ({ action, entityType, entityId, label, expectedText }) => {
      let capturedOptions: ProjectEventStreamOptions | undefined;
      vi.spyOn(API, "openProjectEventStream").mockImplementation((options) => {
        capturedOptions = options;
        return { close: vi.fn() } as unknown as EventSource;
      });
      const debouncedFetchSpy = vi.spyOn(useCostStore.getState(), "debouncedFetch");

      renderHarness("/episodes/1");

      act(() => {
        capturedOptions?.onChanges?.(
          {
            project_name: "demo",
            batch_id: "batch-completion",
            fingerprint: "fp-completion",
            generated_at: "2026-03-01T00:00:00Z",
            source: "worker",
            changes: [
              {
                entity_type: entityType,
                action,
                entity_id: entityId,
                label,
                episode: 1,
                focus: null,
                important: true,
              },
            ],
          },
          new MessageEvent("changes"),
        );
      });

      await waitFor(() => {
        expect(API.getProject).toHaveBeenCalledWith("demo");
        expect(useAppStore.getState().toast?.text).toBe(expectedText);
      });
      expect(useAppStore.getState().toast?.tone).toBe("success");
      expect(screen.getByTestId("location")).toHaveTextContent("/episodes/1");
      expect(useAppStore.getState().scrollTarget).toBeNull();
      expect(debouncedFetchSpy).toHaveBeenCalledWith("demo");
    },
  );

  it("ranks reference_video_ready/tts_ready alongside existing completion events, above entity changes", async () => {
    // CHANGE_PRIORITY 中 reference_video_ready/tts_ready 排在 storyboard_ready/video_ready/grid_ready
    // 之后：同批次多组变更时，toast 状态被逐组覆写，最终展示的应是优先级数值最大（最后处理）的一组。
    let capturedOptions: ProjectEventStreamOptions | undefined;
    vi.spyOn(API, "openProjectEventStream").mockImplementation((options) => {
      capturedOptions = options;
      return { close: vi.fn() } as unknown as EventSource;
    });

    renderHarness("/episodes/1");

    act(() => {
      capturedOptions?.onChanges?.(
        {
          project_name: "demo",
          batch_id: "batch-priority",
          fingerprint: "fp-priority",
          generated_at: "2026-03-01T00:00:00Z",
          source: "worker",
          changes: [
            {
              entity_type: "character",
              action: "created",
              entity_id: "hero",
              label: "角色「hero」",
              focus: null,
              important: true,
            },
            {
              entity_type: "reference_unit",
              action: "reference_video_ready",
              entity_id: "U01",
              label: "参考视频「U01」",
              episode: 1,
              focus: null,
              important: true,
            },
            {
              entity_type: "segment",
              action: "tts_ready",
              entity_id: "E1S01",
              label: "旁白「E1S01」",
              episode: 1,
              focus: null,
              important: true,
            },
          ],
        },
        new MessageEvent("changes"),
      );
    });

    await waitFor(() => {
      expect(API.getProject).toHaveBeenCalledWith("demo");
      expect(useAppStore.getState().toast?.text).toBe("旁白「E1S01」已生成");
    });
  });

  it("groups remote changes by type and invalidates only the touched entity keys", async () => {
    let capturedOptions: ProjectEventStreamOptions | undefined;
    vi.spyOn(API, "openProjectEventStream").mockImplementation((options) => {
      capturedOptions = options;
      return { close: vi.fn() } as unknown as EventSource;
    });

    renderHarness("/");

    act(() => {
      capturedOptions?.onChanges?.(
        {
          project_name: "demo",
          batch_id: "batch-grouped",
          fingerprint: "fp-grouped",
          generated_at: "2026-03-01T00:00:00Z",
          source: "filesystem",
          changes: [
            {
              entity_type: "character",
              action: "created",
              entity_id: "hero",
              label: "角色「hero」",
              focus: {
                pane: "characters",
                anchor_type: "character",
                anchor_id: "hero",
              },
              important: true,
            },
            {
              entity_type: "character",
              action: "created",
              entity_id: "mage",
              label: "角色「mage」",
              focus: {
                pane: "characters",
                anchor_type: "character",
                anchor_id: "mage",
              },
              important: true,
            },
            {
              entity_type: "prop",
              action: "updated",
              entity_id: "玉佩",
              label: "道具「玉佩」",
              focus: {
                pane: "props",
                anchor_type: "prop",
                anchor_id: "玉佩",
              },
              important: true,
            },
          ],
        },
        new MessageEvent("changes"),
      );
    });

    await waitFor(() => {
      expect(API.getProject).toHaveBeenCalledWith("demo");
      expect(useAppStore.getState().toast?.text).toBe("道具「玉佩」已更新");
    });

    expect(useAppStore.getState().getEntityRevision("character:hero")).toBe(1);
    expect(useAppStore.getState().getEntityRevision("character:mage")).toBe(1);
    expect(useAppStore.getState().getEntityRevision("prop:玉佩")).toBe(1);
    expect(useAppStore.getState().getEntityRevision("segment:SEG-404")).toBe(0);
    expect(useAppStore.getState().workspaceNotifications).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          text: "AI 刚新增了 2 个角色：hero、mage，点击查看",
          target: expect.objectContaining({
            type: "character",
            id: "hero",
            route: "/characters",
          }),
        }),
        expect.objectContaining({
          text: "AI 刚更新了 道具「玉佩」，点击查看",
          target: expect.objectContaining({
            type: "prop",
            id: "玉佩",
            route: "/props",
          }),
        }),
      ]),
    );
  });

  it("refreshes without changing focus for webui-originated batches", async () => {
    let capturedOptions: ProjectEventStreamOptions | undefined;
    vi.spyOn(API, "openProjectEventStream").mockImplementation((options) => {
      capturedOptions = options;
      return { close: vi.fn() } as unknown as EventSource;
    });

    renderHarness("/props");

    act(() => {
      capturedOptions?.onChanges?.(
        {
          project_name: "demo",
          batch_id: "batch-3",
          fingerprint: "fp-3",
          generated_at: "2026-03-01T00:00:00Z",
          source: "webui",
          changes: [
            {
              entity_type: "prop",
              action: "updated",
              entity_id: "玉佩",
              label: "道具「玉佩」",
              focus: {
                pane: "props",
                anchor_type: "prop",
                anchor_id: "玉佩",
              },
              important: true,
            },
          ],
        },
        new MessageEvent("changes"),
      );
    });

    await waitFor(() => {
      expect(API.getProject).toHaveBeenCalledWith("demo");
    });
    expect(screen.getByTestId("location")).toHaveTextContent("/props");
    expect(useAppStore.getState().scrollTarget).toBeNull();
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);
  });

  it("defers remote navigation when a workspace edit marker is present", async () => {
    let capturedOptions: ProjectEventStreamOptions | undefined;
    vi.spyOn(API, "openProjectEventStream").mockImplementation((options) => {
      capturedOptions = options;
      return { close: vi.fn() } as unknown as EventSource;
    });

    renderHarness("/characters");
    const editingMarker = document.createElement("div");
    editingMarker.setAttribute("data-workspace-editing", "true");
    document.body.appendChild(editingMarker);

    act(() => {
      capturedOptions?.onChanges?.(
        {
          project_name: "demo",
          batch_id: "batch-4",
          fingerprint: "fp-4",
          generated_at: "2026-03-01T00:00:00Z",
          source: "filesystem",
          changes: [
            {
              entity_type: "scene",
              action: "updated",
              entity_id: "酒馆",
              label: "场景「酒馆」",
              focus: {
                pane: "scenes",
                anchor_type: "scene",
                anchor_id: "酒馆",
              },
              important: true,
            },
          ],
        },
        new MessageEvent("changes"),
      );
    });

    await waitFor(() => {
      expect(useAppStore.getState().workspaceNotifications[0]?.target?.id).toBe("酒馆");
    });
    expect(screen.getByTestId("location")).toHaveTextContent("/characters");
    expect(useAppStore.getState().scrollTarget).toBeNull();
  });

  it("一次不带聚焦目标的刷新（如 onSnapshot）落定时，不应抢先消费更晚一批 onChanges 排队的目标", async () => {
    // onSnapshot 触发的 refreshProject() 不设置新的聚焦目标，落定后按旧逻辑会
    // 无条件 flushQueuedFocus()。若它在途期间，一批带真实目标的 onChanges 已经
    // 到达并排队（改写了 queuedFocusRef，但数据要等它自己那一轮 getProject 完成
    // 才落库），onSnapshot 落定时若仍无条件消费 ref，会拿着尚未落库的目标提前
    // 导航，且清空 ref 后 onChanges 那一批之后不再触发导航。
    let capturedOptions: ProjectEventStreamOptions | undefined;
    vi.spyOn(API, "openProjectEventStream").mockImplementation((options) => {
      capturedOptions = options;
      return { close: vi.fn() } as unknown as EventSource;
    });

    const d1 = deferred<GetProjectResult>();
    const d2 = deferred<GetProjectResult>();
    const getProjectSpy = vi
      .spyOn(API, "getProject")
      .mockReturnValueOnce(d1.promise)
      .mockReturnValueOnce(d2.promise);

    renderHarness("/");

    // 建立初始 fingerprint 基线（首次 onSnapshot 不触发刷新）。
    act(() => {
      capturedOptions?.onSnapshot?.(
        { project_name: "demo", fingerprint: "fp-a", generated_at: "2026-03-01T00:00:00Z" },
        new MessageEvent("snapshot"),
      );
    });
    expect(getProjectSpy).not.toHaveBeenCalled();

    // fingerprint 变化触发一次不带聚焦目标的刷新，getProject 卡在在途（d1 未 resolve）。
    act(() => {
      capturedOptions?.onSnapshot?.(
        { project_name: "demo", fingerprint: "fp-b", generated_at: "2026-03-01T00:00:01Z" },
        new MessageEvent("snapshot"),
      );
    });
    expect(getProjectSpy).toHaveBeenCalledTimes(1);

    // 在途期间，一批带真实聚焦目标的 onChanges 到达并排队。
    act(() => {
      capturedOptions?.onChanges?.(
        {
          project_name: "demo",
          batch_id: "batch-race",
          fingerprint: "fp-race",
          generated_at: "2026-03-01T00:00:00Z",
          source: "filesystem",
          changes: [
            {
              entity_type: "character",
              action: "created",
              entity_id: "hero",
              label: "角色「hero」",
              focus: { pane: "characters", anchor_type: "character", anchor_id: "hero" },
              important: true,
            },
          ],
        },
        new MessageEvent("changes"),
      );
    });
    // 在途合并：这批只是排队，不会立即多发一次请求。
    expect(getProjectSpy).toHaveBeenCalledTimes(1);

    // onSnapshot 那一轮落定：不应提前导航到 onChanges 排队的目标（/characters）。
    await act(async () => {
      d1.resolve(makeGetProjectResult("R1"));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByTestId("location")).toHaveTextContent("/");

    // onChanges 排队的那一轮落定：导航到它真正的目标，且只补发了这一次请求。
    await act(async () => {
      d2.resolve(makeGetProjectResult("R2"));
      await Promise.resolve();
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/characters");
    });
    expect(getProjectSpy).toHaveBeenCalledTimes(2);
  });

  it("does not navigate to a stale focus target once a later SSE batch has queued a newer one", async () => {
    // 复现:两批 onChanges 重叠到达,第一批的 refreshProject 仍在途(getProject 未落定)时
    // 第二批已到达并把 queuedFocusRef 改写为自己的目标。第一批落定后不应消费已被取代的
    // ref 值提前导航;要等第二批自己那一轮落定才导航到第二批的目标。
    let capturedOptions: ProjectEventStreamOptions | undefined;
    vi.spyOn(API, "openProjectEventStream").mockImplementation((options) => {
      capturedOptions = options;
      return { close: vi.fn() } as unknown as EventSource;
    });

    const d1 = deferred<GetProjectResult>();
    const d2 = deferred<GetProjectResult>();
    const getProjectSpy = vi
      .spyOn(API, "getProject")
      .mockReturnValueOnce(d1.promise)
      .mockReturnValueOnce(d2.promise);

    renderHarness("/");

    // 第一批:聚焦角色 hero → /characters。发起后 getProject 卡在在途(d1 未 resolve)。
    act(() => {
      capturedOptions?.onChanges?.(
        {
          project_name: "demo",
          batch_id: "batch-stale",
          fingerprint: "fp-stale-1",
          generated_at: "2026-03-01T00:00:00Z",
          source: "filesystem",
          changes: [
            {
              entity_type: "character",
              action: "created",
              entity_id: "hero",
              label: "角色「hero」",
              focus: { pane: "characters", anchor_type: "character", anchor_id: "hero" },
              important: true,
            },
          ],
        },
        new MessageEvent("changes"),
      );
    });
    expect(getProjectSpy).toHaveBeenCalledTimes(1);

    // 第二批在第一批仍在途时到达:聚焦场景 酒馆 → /scenes，覆盖 queuedFocusRef。
    act(() => {
      capturedOptions?.onChanges?.(
        {
          project_name: "demo",
          batch_id: "batch-stale-2",
          fingerprint: "fp-stale-2",
          generated_at: "2026-03-01T00:00:00Z",
          source: "filesystem",
          changes: [
            {
              entity_type: "scene",
              action: "updated",
              entity_id: "酒馆",
              label: "场景「酒馆」",
              focus: { pane: "scenes", anchor_type: "scene", anchor_id: "酒馆" },
              important: true,
            },
          ],
        },
        new MessageEvent("changes"),
      );
    });
    // 在途合并:第二批只是排队，不会立即多发一次请求。
    expect(getProjectSpy).toHaveBeenCalledTimes(1);

    // 第一轮落定：不应提前导航到已被取代的第一批目标（/characters）。
    await act(async () => {
      d1.resolve(makeGetProjectResult("R1"));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByTestId("location")).toHaveTextContent("/");

    // 排队轮（第二批）落定：导航到第二批真正的目标（/scenes），且只发了这一次补充请求。
    await act(async () => {
      d2.resolve(makeGetProjectResult("R2"));
      await Promise.resolve();
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/scenes");
    });
    expect(getProjectSpy).toHaveBeenCalledTimes(2);
  });

  it("extracts asset_fingerprints from SSE changes and updates store", async () => {
    let capturedOptions: ProjectEventStreamOptions | undefined;
    vi.spyOn(API, "openProjectEventStream").mockImplementation((options) => {
      capturedOptions = options;
      return { close: vi.fn() } as unknown as EventSource;
    });

    renderHarness("/");

    act(() => {
      capturedOptions?.onChanges?.(
        {
          project_name: "demo",
          batch_id: "batch-fp",
          fingerprint: "fp-fp",
          generated_at: "2026-03-01T00:00:00Z",
          source: "worker",
          changes: [
            {
              entity_type: "segment",
              action: "storyboard_ready",
              entity_id: "E1S01",
              label: "分镜「E1S01」",
              focus: null,
              important: true,
              asset_fingerprints: { "storyboards/scene_E1S01.png": 1710288000 },
            },
          ],
        },
        new MessageEvent("changes"),
      );
    });

    // fingerprints 应立即（同步）写入 store，无需等待 getProject
    expect(useProjectsStore.getState().getAssetFingerprint("storyboards/scene_E1S01.png")).toBe(1710288000);
  });

  it("stops the reconnect loop after the project_deleted termination event", async () => {
    let capturedOptions: ProjectEventStreamOptions | undefined;
    const closeMock = vi.fn();
    const openSpy = vi.spyOn(API, "openProjectEventStream").mockImplementation((options) => {
      capturedOptions = options;
      return { close: closeMock } as unknown as EventSource;
    });

    renderHarness("/");
    expect(openSpy).toHaveBeenCalledTimes(1);

    act(() => {
      capturedOptions?.onProjectDeleted?.(
        { project_name: "demo" },
        new MessageEvent("project_deleted"),
      );
    });
    expect(closeMock).toHaveBeenCalledTimes(1);

    vi.useFakeTimers();
    try {
      // 浏览器原生行为：流被服务端关闭后，EventSource 紧接着会触发一次 onerror；
      // terminatedRef 应拦住它排的重连，即便等过了原本的 3s 重连延迟。
      act(() => {
        capturedOptions?.onError?.(new Event("error"));
      });
      act(() => {
        vi.advanceTimersByTime(5000);
      });
    } finally {
      vi.useRealTimers();
    }

    expect(openSpy).toHaveBeenCalledTimes(1);
  });

  it("clears an already-pending reconnect timer when the project_deleted event arrives", async () => {
    let capturedOptions: ProjectEventStreamOptions | undefined;
    const closeMock = vi.fn();
    const openSpy = vi.spyOn(API, "openProjectEventStream").mockImplementation((options) => {
      capturedOptions = options;
      return { close: closeMock } as unknown as EventSource;
    });

    renderHarness("/");
    expect(openSpy).toHaveBeenCalledTimes(1);

    vi.useFakeTimers();
    try {
      // 先触发一次普通 onError，排入 3s 后的重连定时器。
      act(() => {
        capturedOptions?.onError?.(new Event("error"));
      });

      // 定时器排队期间收到终止事件：onProjectDeleted 应清掉这个待触发的重连定时器，
      // 而不仅是处理之后新触发的 onError（见上一条用例）。
      act(() => {
        capturedOptions?.onProjectDeleted?.(
          { project_name: "demo" },
          new MessageEvent("project_deleted"),
        );
      });

      act(() => {
        vi.advanceTimersByTime(5000);
      });
    } finally {
      vi.useRealTimers();
    }

    // 若定时器未被清除，会在 3s 时触发 connect() 导致 openSpy 被再次调用。
    expect(openSpy).toHaveBeenCalledTimes(1);
  });
});
