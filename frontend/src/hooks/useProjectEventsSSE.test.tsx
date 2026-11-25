import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router, useLocation } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API, type ProjectEventStreamOptions } from "@/api";
import { LeaveGuardProvider, useLeaveGuard } from "@/components/shared/edit-unit/LeaveGuard";
import { useProjectEventsSSE } from "./useProjectEventsSSE";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useItemLocationsStore } from "@/stores/item-locations-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useCostStore } from "@/stores/cost-store";
import { useTasksStore } from "@/stores/tasks-store";
import type { ProjectChange } from "@/types";
import { seedTasks } from "@/test/task-state";
import { makeTask, makeWorkspaceResponse } from "@/test/factories";
import { createDeferred } from "@/test/deferred";
import { FakeSseStream } from "@/test/fakeSseStream";

function mockProjectEventStream() {
  let capturedOptions: ProjectEventStreamOptions | undefined;
  FakeSseStream.reset();
  const openSpy = vi.spyOn(API, "openProjectEventStream").mockImplementation((options) => {
    capturedOptions = options;
    return new FakeSseStream();
  });
  return {
    get options() {
      return capturedOptions;
    },
    get source() {
      return FakeSseStream.instances[FakeSseStream.instances.length - 1];
    },
    get close() {
      return this.source.close;
    },
    openSpy,
  };
}

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

type GetWorkspaceResult = Awaited<ReturnType<typeof API.getWorkspace>>;

function makeGetWorkspaceResult(title: string): GetWorkspaceResult {
  return makeWorkspaceResponse({
    title,
    content_mode: "narration",
    style: "Anime",
    episodes: [],
    characters: { hero: { description: "勇者" } },
    scenes: { 酒馆: { description: "小镇酒馆" } },
    props: {},
  });
}

describe("useProjectEventsSSE", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    useAppStore.setState(useAppStore.getInitialState(), true);
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useItemLocationsStore.getState().reset(null);
    vi.restoreAllMocks();
    vi.spyOn(API, "getWorkspace").mockResolvedValue(
      makeWorkspaceResponse({
        title: "Demo",
        content_mode: "narration",
        style: "Anime",
        episodes: [{ episode: 1, title: "第一集", script_file: "scripts/episode_1.json", script_status: "generated" }],
        characters: { hero: { description: "勇者" } },
        scenes: {},
        props: {},
      }),
    );
  });

  it("refreshes and navigates to the focused workspace target for remote changes", async () => {
    const stream = mockProjectEventStream();

    renderHarness("/");
    expect(stream.options).toBeDefined();
    expect(stream.options?.projectName).toBe("demo");

    act(() => {
      stream.options?.onChanges?.(
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
              label: "backend fallback",
              label_key: "named_entity_character",
              label_params: { id: "hero" },
              focus: {
                pane: "characters",
                anchor_type: "character",
                anchor_id: "hero",
              },
              important: true,
            },
          ],
        },
      );
    });

    await waitFor(() => {
      expect(API.getWorkspace).toHaveBeenCalledWith("demo", { signal: expect.any(AbortSignal) });
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

  it("navigates product changes to the products page", async () => {
    const stream = mockProjectEventStream();

    renderHarness("/");

    act(() => {
      stream.options?.onChanges?.({
        project_name: "demo",
        batch_id: "batch-product",
        fingerprint: "fp-product",
        generated_at: "2026-03-01T00:00:00Z",
        source: "filesystem",
        changes: [
          {
            entity_type: "product",
            action: "created",
            entity_id: "咖啡",
            label: "backend fallback",
            label_key: "named_entity_product",
            label_params: { id: "咖啡" },
            focus: { pane: "products", anchor_type: "product", anchor_id: "咖啡" },
            important: true,
          },
        ],
      });
    });

    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/products");
    });
    expect(useAppStore.getState().workspaceNotifications[0]).toEqual(
      expect.objectContaining({
        text: "AI 刚新增了 商品「咖啡」，点击查看",
        target: expect.objectContaining({ type: "product", id: "咖啡", route: "/products" }),
      }),
    );
  });

  it("navigates reference video units to the reference canvas via reference_unit target", async () => {
    const stream = mockProjectEventStream();

    renderHarness("/episodes/1");

    act(() => {
      stream.options?.onChanges?.(
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
      );
    });

    await waitFor(() => {
      expect(API.getWorkspace).toHaveBeenCalledWith("demo", { signal: expect.any(AbortSignal) });
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
    const stream = mockProjectEventStream();

    renderHarness("/");
    const input = document.createElement("input");
    document.body.appendChild(input);
    input.focus();

    act(() => {
      stream.options?.onChanges?.(
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
      );
    });

    await waitFor(() => {
      expect(API.getWorkspace).toHaveBeenCalledWith("demo", { signal: expect.any(AbortSignal) });
      expect(useAppStore.getState().workspaceNotifications[0]?.target?.id).toBe("酒馆");
    });
    expect(screen.getByTestId("location")).toHaveTextContent("/");
    expect(useAppStore.getState().scrollTarget).toBeNull();
  });

  it("recovers active tasks on reconnect through the shared refresh", async () => {
    const row = makeTask({ project_name: "demo" });
    const snapshot = vi.spyOn(API, "getActiveTasks").mockResolvedValue({ items: [row], truncated: false });
    vi.spyOn(API, "listTasks").mockResolvedValue({ items: [], total: 0, page: 1, page_size: 200 });
    vi.spyOn(API, "getTaskStats").mockResolvedValue({ stats: useTasksStore.getInitialState().stats });
    useTasksStore.getState().setRefreshScope({ projectName: "demo" });
    const stream = mockProjectEventStream();
    renderHarness();
    const emit = () => stream.options?.onSnapshot?.({ project_name: "demo", fingerprint: "same" } as never);
    act(emit);
    expect(snapshot).not.toHaveBeenCalled();
    await act(async () => { emit(); });
    expect(useTasksStore.getState().activeTaskIds.has(row.task_id)).toBe(true);
    expect(snapshot).toHaveBeenCalledTimes(1);
    useTasksStore.getState().setRefreshScope(null);
  });

  it("invalidates episode script plan views when a reconnect snapshot shows missed changes", () => {
    useProjectsStore.setState({
      currentProjectData: { ...makeGetWorkspaceResult("Demo").project, episodes: [{ episode: 4, title: "雨夜" }] },
    } as never);
    const stream = mockProjectEventStream();
    renderHarness("/");
    const revision = () => useAppStore.getState().getEntityRevision("script_plan:episode_4");

    act(() => stream.options?.onSnapshot?.({ project_name: "demo", fingerprint: "fp-a" } as never));
    const before = revision();
    act(() => stream.options?.onSnapshot?.({ project_name: "demo", fingerprint: "fp-b" } as never));

    expect(revision()).toBeGreaterThan(before);
  });

  it("invalidates episode script_plan revisions when script_plan or dependency assets change", async () => {
    useProjectsStore.setState({
      currentProjectData: { ...makeGetWorkspaceResult("Demo").project, episodes: [{ episode: 1, title: "第一集" }] },
    } as never);
    const stream = mockProjectEventStream();
    renderHarness("/");
    const rev = () => useAppStore.getState().getEntityRevision("script_plan:episode_1");
    const initial = rev();

    act(() => {
      stream.options?.onChanges?.({
        project_name: "demo",
        batch_id: "batch-sp",
        fingerprint: "fp-sp",
        generated_at: "2026-03-01T00:00:00Z",
        source: "filesystem",
        changes: [
          {
            entity_type: "script_plan",
            action: "updated",
            entity_id: "episode_1",
            label: "第一集的脚本规划",
            label_key: "script_plan",
            label_params: { episode: 1 },
            episode: 1,
            focus: null,
            important: false,
          },
        ],
      });
    });

    const afterPatch = rev();
    expect(afterPatch).toBeGreaterThan(initial);

    act(() => {
      stream.options?.onChanges?.({
        project_name: "demo",
        batch_id: "batch-char",
        fingerprint: "fp-char",
        generated_at: "2026-03-01T00:00:01Z",
        source: "filesystem",
        changes: [
          {
            entity_type: "character",
            action: "created",
            entity_id: "新角色",
            label: "角色「新角色」",
            label_key: "named_entity_character",
            label_params: { id: "新角色" },
            focus: null,
            important: true,
          },
        ],
      });
    });

    expect(rev()).toBeGreaterThan(afterPatch);
  });

  it("updates text progress without refreshing the task list", () => {
    const row = makeTask({ task_id: "text", project_name: "demo", status: "running" });
    seedTasks([row]);
    const refresh = vi.spyOn(useTasksStore.getState(), "refreshTasks");
    const stream = mockProjectEventStream();
    renderHarness();
    const event = { task_id: "text", phase: "waiting" as const, seq: 1, model: "m" };
    act(() => stream.options?.onTaskProgress?.(event));
    expect(useTasksStore.getState().progressById.get("text")).toEqual(event);
    expect(refresh).not.toHaveBeenCalled();
  });

  it("forwards an assistant session resuming on its own to the assistant store", () => {
    useAssistantStore.setState(useAssistantStore.getInitialState(), true);
    const stream = mockProjectEventStream();
    renderHarness("/");

    act(() =>
      stream.options?.onAssistantSessionResumed?.({ project_name: "demo", session_id: "session-1", status: "running" }),
    );

    expect(useAssistantStore.getState().sessionResumeSignals).toEqual([
      { kind: "resumed", projectName: "demo", sessionId: "session-1" },
    ]);
  });

  it("asks the assistant to re-check its session on every snapshot, the first one included", () => {
    useAssistantStore.setState(useAssistantStore.getInitialState(), true);
    const stream = mockProjectEventStream();
    renderHarness("/");

    act(() => stream.options?.onSnapshot?.({ project_name: "demo", fingerprint: "fp-a" } as never));
    act(() => stream.options?.onSnapshot?.({ project_name: "demo", fingerprint: "fp-a" } as never));

    expect(useAssistantStore.getState().sessionResumeSignals).toEqual([
      { kind: "resync", projectName: "demo" },
      { kind: "resync", projectName: "demo" },
    ]);
  });

  it("names an episode the Agent just created from the refreshed ledger", async () => {
    const stream = mockProjectEventStream();

    renderHarness("/");
    act(() => {
      stream.options?.onChanges?.({
        project_name: "demo",
        batch_id: "batch-episode",
        fingerprint: "fp-episode",
        generated_at: "2026-03-01T00:00:00Z",
        source: "filesystem",
        changes: [
          {
            entity_type: "episode",
            action: "created",
            entity_id: "1",
            label: "集（id=1）",
            label_key: "episode",
            label_params: { episode: 1 },
            episode: 1,
            focus: null,
            important: true,
          },
        ],
      });
    });

    await waitFor(() => {
      expect(useAppStore.getState().workspaceNotifications.map((n) => n.text).join("\n")).toContain("第一集");
    });
    expect(useAppStore.getState().workspaceNotifications.map((n) => n.text).join("\n")).not.toContain("未命名集");
  });

  it("does not announce changes from a stale script when the refresh fails", async () => {
    vi.spyOn(API, "getWorkspace").mockRejectedValue(new Error("network down"));
    const stream = mockProjectEventStream();

    renderHarness("/");
    act(() => {
      stream.options?.onChanges?.({
        project_name: "demo",
        batch_id: "batch-created-segment",
        fingerprint: "fp-created-segment",
        generated_at: "2026-03-01T00:00:00Z",
        source: "filesystem",
        changes: [
          {
            entity_type: "segment",
            action: "created",
            entity_id: "E1S07",
            label: "分镜「E1S07」",
            label_key: "segment",
            label_params: { id: "E1S07" },
            episode: 1,
            focus: null,
            important: true,
          },
        ],
      });
    });

    await waitFor(() => {
      expect(useAppStore.getState().toast?.tone).toBe("warning");
    });
    expect(useAppStore.getState().toast?.text).toContain("network down");
    expect(useAppStore.getState().workspaceNotifications.map((n) => n.text).join("\n")).not.toContain("已删除");
  });

  it("does not navigate or scroll and clears stale focus when refresh fails with a non-null focus target", async () => {
    vi.spyOn(API, "getWorkspace")
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValueOnce(makeGetWorkspaceResult("Recovered"));
    const stream = mockProjectEventStream();

    renderHarness("/");
    act(() => {
      stream.options?.onChanges?.({
        project_name: "demo",
        batch_id: "batch-fail-focus",
        fingerprint: "fp-fail-focus",
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
      });
    });

    await waitFor(() => {
      expect(useAppStore.getState().toast?.tone).toBe("warning");
    });
    expect(useAppStore.getState().toast?.text).toContain("network down");
    expect(screen.getByTestId("location")).toHaveTextContent("/");
    expect(useAppStore.getState().scrollTarget).toBeNull();
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(1);
    expect(useAppStore.getState().workspaceNotifications[0]).toEqual(
      expect.objectContaining({ tone: "warning", target: null }),
    );

    act(() => {
      stream.options?.onSnapshot?.({
        project_name: "demo",
        fingerprint: "fp-recovered",
        generated_at: "2026-03-01T00:00:01Z",
      });
    });

    await waitFor(() => {
      expect(useProjectsStore.getState().currentProjectData?.title).toBe("Recovered");
    });
    expect(API.getWorkspace).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("location")).toHaveTextContent("/");
    expect(useAppStore.getState().scrollTarget).toBeNull();
  });

  it("stays silent and clears stale focus when refresh is cancelled with a non-null focus target", async () => {
    const d1 = createDeferred<GetWorkspaceResult>();
    const getProjectSpy = vi
      .spyOn(API, "getWorkspace")
      .mockReturnValueOnce(d1.promise)
      .mockResolvedValueOnce(makeGetWorkspaceResult("Recovered"));
    const stream = mockProjectEventStream();

    renderHarness("/");
    act(() => {
      stream.options?.onChanges?.({
        project_name: "demo",
        batch_id: "batch-cancel-focus",
        fingerprint: "fp-cancel-focus",
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
      });
    });

    expect(getProjectSpy).toHaveBeenCalledTimes(1);
    act(() => {
      useProjectsStore.getState().setCurrentProject("other", null);
    });
    await act(async () => {
      d1.reject(new DOMException("aborted", "AbortError"));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(useAppStore.getState().toast).toBeNull();
    expect(screen.getByTestId("location")).toHaveTextContent("/");
    expect(useAppStore.getState().scrollTarget).toBeNull();
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);

    act(() => {
      useProjectsStore.getState().setCurrentProject("demo", null);
      stream.options?.onSnapshot?.({
        project_name: "demo",
        fingerprint: "fp-after-cancel",
        generated_at: "2026-03-01T00:00:01Z",
      });
    });

    await waitFor(() => {
      expect(useProjectsStore.getState().currentProjectData?.title).toBe("Recovered");
    });
    expect(getProjectSpy).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("location")).toHaveTextContent("/");
    expect(useAppStore.getState().scrollTarget).toBeNull();
  });

  it("preserves a newer queued focus target when an earlier in-flight refresh fails", async () => {
    const stream = mockProjectEventStream();
    const d1 = createDeferred<GetWorkspaceResult>();
    const d2 = createDeferred<GetWorkspaceResult>();
    const getProjectSpy = vi
      .spyOn(API, "getWorkspace")
      .mockReturnValueOnce(d1.promise)
      .mockReturnValueOnce(d2.promise);

    renderHarness("/");

    act(() => {
      stream.options?.onChanges?.({
        project_name: "demo",
        batch_id: "batch-fail-1",
        fingerprint: "fp-fail-1",
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
      });
    });
    expect(getProjectSpy).toHaveBeenCalledTimes(1);

    act(() => {
      stream.options?.onChanges?.({
        project_name: "demo",
        batch_id: "batch-ok-2",
        fingerprint: "fp-ok-2",
        generated_at: "2026-03-01T00:00:01Z",
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
      });
    });
    expect(getProjectSpy).toHaveBeenCalledTimes(1);

    await act(async () => {
      d1.reject(new Error("first batch failed"));
      await Promise.resolve();
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(useAppStore.getState().toast?.tone).toBe("warning");
    });
    expect(screen.getByTestId("location")).toHaveTextContent("/");
    expect(useAppStore.getState().scrollTarget).toBeNull();

    await act(async () => {
      d2.resolve(makeGetWorkspaceResult("R2"));
      await Promise.resolve();
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/scenes");
    });
    expect(useAppStore.getState().scrollTarget).toEqual(
      expect.objectContaining({
        type: "scene",
        id: "酒馆",
        route: "/scenes",
      }),
    );
    expect(getProjectSpy).toHaveBeenCalledTimes(2);
  });

  it("shows a toast without navigation for generation completion batches", async () => {
    const stream = mockProjectEventStream();

    renderHarness("/episodes/1");

    act(() => {
      stream.options?.onChanges?.(
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
      );
    });

    await waitFor(() => {
      expect(API.getWorkspace).toHaveBeenCalledWith("demo", { signal: expect.any(AbortSignal) });
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
      label: "多宫格分镜「G01」",
      expectedText: "多宫格分镜「G01」已生成",
    },
    {
      action: "reference_video_ready" as const,
      entityType: "reference_unit" as const,
      entityId: "U01",
      label: "视频单元「U01」",
      expectedText: "视频单元「U01」已生成",
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
      const stream = mockProjectEventStream();
      const debouncedFetchSpy = vi.spyOn(useCostStore.getState(), "debouncedFetch");

      renderHarness("/episodes/1");

      act(() => {
        stream.options?.onChanges?.(
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
        );
      });

      await waitFor(() => {
        expect(API.getWorkspace).toHaveBeenCalledWith("demo", { signal: expect.any(AbortSignal) });
        expect(useAppStore.getState().toast?.text).toBe(expectedText);
      });
      expect(useAppStore.getState().toast?.tone).toBe("success");
      expect(screen.getByTestId("location")).toHaveTextContent("/episodes/1");
      expect(useAppStore.getState().scrollTarget).toBeNull();
      expect(debouncedFetchSpy).toHaveBeenCalledWith("demo");
    },
  );

  it("ranks reference_video_ready/tts_ready alongside existing completion events, above entity changes", async () => {
    const stream = mockProjectEventStream();

    renderHarness("/episodes/1");

    act(() => {
      stream.options?.onChanges?.(
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
              label: "视频单元「U01」",
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
      );
    });

    await waitFor(() => {
      expect(API.getWorkspace).toHaveBeenCalledWith("demo", { signal: expect.any(AbortSignal) });
      expect(useAppStore.getState().toast?.text).toBe("旁白「E1S01」已生成");
    });
  });

  it("groups remote changes by type and invalidates only the touched entity keys", async () => {
    const stream = mockProjectEventStream();

    renderHarness("/");

    act(() => {
      stream.options?.onChanges?.(
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
      );
    });

    await waitFor(() => {
      expect(API.getWorkspace).toHaveBeenCalledWith("demo", { signal: expect.any(AbortSignal) });
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
    const stream = mockProjectEventStream();

    renderHarness("/props");

    act(() => {
      stream.options?.onChanges?.(
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
      );
    });

    await waitFor(() => {
      expect(API.getWorkspace).toHaveBeenCalledWith("demo", { signal: expect.any(AbortSignal) });
    });
    expect(screen.getByTestId("location")).toHaveTextContent("/props");
    expect(useAppStore.getState().scrollTarget).toBeNull();
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);
  });

  it("defers remote navigation when a workspace edit marker is present", async () => {
    const stream = mockProjectEventStream();

    renderHarness("/characters");
    const editingMarker = document.createElement("div");
    editingMarker.setAttribute("data-workspace-editing", "true");
    document.body.appendChild(editingMarker);

    act(() => {
      stream.options?.onChanges?.(
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
      );
    });

    await waitFor(() => {
      expect(useAppStore.getState().workspaceNotifications[0]?.target?.id).toBe("酒馆");
    });
    expect(screen.getByTestId("location")).toHaveTextContent("/characters");
    expect(useAppStore.getState().scrollTarget).toBeNull();
  });

  it("有编辑单元带着未保存修改时，Agent 改动不触发自动跳转与定位", async () => {
    const stream = mockProjectEventStream();
    const saveNothing = async () => true;
    function DirtyUnit() {
      useLeaveGuard({ dirty: true, save: saveNothing });
      return null;
    }
    const { hook } = memoryLocation({ path: "/episodes/1" });
    render(
      <Router hook={hook}>
        <LeaveGuardProvider>
          <DirtyUnit />
          <HookHarness projectName="demo" />
        </LeaveGuardProvider>
      </Router>,
    );

    act(() => {
      stream.options?.onChanges?.({
        project_name: "demo",
        batch_id: "batch-dirty",
        fingerprint: "fp-dirty",
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
      });
    });

    await waitFor(() => {
      expect(useAppStore.getState().workspaceNotifications[0]?.target?.id).toBe("酒馆");
    });
    expect(screen.getByTestId("location")).toHaveTextContent("/episodes/1");
    expect(useAppStore.getState().scrollTarget).toBeNull();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("一次不带聚焦目标的刷新（如 onSnapshot）落定时，不应抢先消费更晚一批 onChanges 排队的目标", async () => {
    const stream = mockProjectEventStream();

    const d1 = createDeferred<GetWorkspaceResult>();
    const d2 = createDeferred<GetWorkspaceResult>();
    const getProjectSpy = vi
      .spyOn(API, "getWorkspace")
      .mockReturnValueOnce(d1.promise)
      .mockReturnValueOnce(d2.promise);

    renderHarness("/");

    act(() => {
      stream.options?.onSnapshot?.(
        { project_name: "demo", fingerprint: "fp-a", generated_at: "2026-03-01T00:00:00Z" },
      );
    });
    expect(getProjectSpy).not.toHaveBeenCalled();

    act(() => {
      stream.options?.onSnapshot?.(
        { project_name: "demo", fingerprint: "fp-b", generated_at: "2026-03-01T00:00:01Z" },
      );
    });
    expect(getProjectSpy).toHaveBeenCalledTimes(1);

    act(() => {
      stream.options?.onChanges?.(
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
      );
    });
    expect(getProjectSpy).toHaveBeenCalledTimes(1);

    await act(async () => {
      d1.resolve(makeGetWorkspaceResult("R1"));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByTestId("location")).toHaveTextContent("/");

    await act(async () => {
      d2.resolve(makeGetWorkspaceResult("R2"));
      await Promise.resolve();
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/characters");
    });
    expect(getProjectSpy).toHaveBeenCalledTimes(2);
  });

  it("does not navigate to a stale focus target once a later SSE batch has queued a newer one", async () => {
    const stream = mockProjectEventStream();

    const d1 = createDeferred<GetWorkspaceResult>();
    const d2 = createDeferred<GetWorkspaceResult>();
    const getProjectSpy = vi
      .spyOn(API, "getWorkspace")
      .mockReturnValueOnce(d1.promise)
      .mockReturnValueOnce(d2.promise);

    renderHarness("/");

    act(() => {
      stream.options?.onChanges?.(
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
      );
    });
    expect(getProjectSpy).toHaveBeenCalledTimes(1);

    act(() => {
      stream.options?.onChanges?.(
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
      );
    });
    expect(getProjectSpy).toHaveBeenCalledTimes(1);

    await act(async () => {
      d1.resolve(makeGetWorkspaceResult("R1"));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByTestId("location")).toHaveTextContent("/");

    await act(async () => {
      d2.resolve(makeGetWorkspaceResult("R2"));
      await Promise.resolve();
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/scenes");
    });
    expect(getProjectSpy).toHaveBeenCalledTimes(2);
  });

  it("extracts asset_fingerprints from SSE changes and updates store", async () => {
    const stream = mockProjectEventStream();

    renderHarness("/");

    act(() => {
      stream.options?.onChanges?.(
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
      );
    });

    expect(useProjectsStore.getState().getAssetFingerprint("storyboards/scene_E1S01.png")).toBe(1710288000);
  });

  it("closes the stream handle after the project_deleted termination event", () => {
    const stream = mockProjectEventStream();

    renderHarness("/");
    expect(stream.openSpy).toHaveBeenCalledTimes(1);

    act(() => {
      stream.options?.onProjectDeleted?.({ project_name: "demo" });
    });

    expect(stream.close).toHaveBeenCalledTimes(1);
    expect(stream.source.closed).toBe(true);
    expect(stream.openSpy).toHaveBeenCalledTimes(1);
  });

  it("closes the stream handle on unmount", () => {
    const stream = mockProjectEventStream();

    const view = renderHarness("/");
    expect(stream.openSpy).toHaveBeenCalledTimes(1);
    expect(stream.close).not.toHaveBeenCalled();

    view.unmount();

    expect(stream.close).toHaveBeenCalledTimes(1);
  });

  describe("任务终态事件", () => {
    function taskChange(overrides: Partial<ProjectChange> = {}): ProjectChange {
      return {
        entity_type: "task",
        action: "task_succeeded",
        entity_id: "task-1",
        label: "task-1",
        focus: null,
        important: false,
        ...overrides,
      };
    }

    function emit(
      stream: ReturnType<typeof mockProjectEventStream>,
      changes: ProjectChange[],
    ) {
      act(() => {
        stream.options?.onChanges?.(
          {
            project_name: "demo",
            batch_id: "batch-task",
            fingerprint: "fp-task",
            generated_at: "2026-03-01T00:00:00Z",
            source: "worker",
            changes,
          },
        );
      });
    }

    it("收到提示词编写完成事件即刷新参考生视频单元列表", () => {
      const stream = mockProjectEventStream();
      vi.spyOn(useTasksStore.getState(), "refreshTasks").mockResolvedValue(undefined);
      renderHarness();
      const before = useAppStore.getState().referenceVideoUnitsRevision;

      emit(stream, [taskChange({ action: "task_succeeded", task_type: "text_episode_script" })]);

      expect(useAppStore.getState().referenceVideoUnitsRevision).toBeGreaterThan(before);
    });

    it("收到任务终态即刷新任务列表，不等兜底轮询", async () => {
      const stream = mockProjectEventStream();
      const refreshTasksSpy = vi
        .spyOn(useTasksStore.getState(), "refreshTasks")
        .mockResolvedValue(undefined);

      renderHarness("/");
      emit(stream, [taskChange()]);

      expect(refreshTasksSpy).toHaveBeenCalledTimes(1);
    });

    it("纯任务终态批次不重拉项目，仍刷新任务列表", async () => {
      const stream = mockProjectEventStream();
      const refreshTasksSpy = vi
        .spyOn(useTasksStore.getState(), "refreshTasks")
        .mockResolvedValue(undefined);
      const getProjectSpy = vi.spyOn(API, "getWorkspace");

      renderHarness("/");
      emit(stream, [
        taskChange(),
        taskChange({ entity_id: "task-2", action: "task_failed" }),
      ]);

      expect(refreshTasksSpy).toHaveBeenCalledTimes(1);
      expect(getProjectSpy).not.toHaveBeenCalled();
    });

    it("批次混有项目实体变更时照常重拉项目数据", async () => {
      const stream = mockProjectEventStream();
      const refreshTasksSpy = vi
        .spyOn(useTasksStore.getState(), "refreshTasks")
        .mockResolvedValue(undefined);

      renderHarness("/episodes/1");
      emit(stream, [
        taskChange(),
        {
          entity_type: "segment",
          action: "video_ready",
          entity_id: "E1S01",
          label: "分镜「E1S01」",
          episode: 1,
          focus: null,
          important: true,
        },
      ]);

      await waitFor(() => {
        expect(API.getWorkspace).toHaveBeenCalledWith("demo", { signal: expect.any(AbortSignal) });
      });
      expect(refreshTasksSpy).toHaveBeenCalled();
    });

    it("任务终态不弹通知、不触发聚焦跳转（important=false / focus=null）", async () => {
      const stream = mockProjectEventStream();
      vi.spyOn(useTasksStore.getState(), "refreshTasks").mockResolvedValue(undefined);

      renderHarness("/");
      emit(stream, [taskChange()]);

      expect(useAppStore.getState().toast).toBeNull();
      expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);
      expect(useAppStore.getState().scrollTarget).toBeNull();
      expect(screen.getByTestId("location")).toHaveTextContent("/");
    });

    it("参考生视频任务成功让分组失效，画布据此重拉成片", async () => {
      const stream = mockProjectEventStream();
      vi.spyOn(useTasksStore.getState(), "refreshTasks").mockResolvedValue(undefined);

      renderHarness("/");
      emit(stream, [taskChange({ task_type: "reference_video" })]);

      expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(1);
    });

    it.each(["created" as const, "updated" as const, "deleted" as const])(
      "reference_unit:%s 让独立分组缓存失效",
      async (action) => {
        const stream = mockProjectEventStream();

        renderHarness("/");
        emit(stream, [
          {
            entity_type: "reference_unit",
            action,
            entity_id: "E1U1",
            label: "视频单元「E1U1」",
            episode: 1,
            focus: null,
            important: false,
          },
        ]);

        expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(1);
      },
    );

    it.each(["character", "scene", "prop", "product"] as const)(
      "资产 %s 变更让分组缓存失效：单元可用参考图与所落的桶要重拉",
      async (entityType) => {
        const stream = mockProjectEventStream();

        renderHarness("/");
        emit(stream, [
          {
            entity_type: entityType,
            action: "updated",
            entity_id: "阿离",
            label: "资产「阿离」",
            focus: null,
            important: false,
          },
        ]);

        expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(1);
      },
    );

    it("同批 unit 变更与生成成功只让分组缓存失效一次", async () => {
      const stream = mockProjectEventStream();
      vi.spyOn(useTasksStore.getState(), "refreshTasks").mockResolvedValue(undefined);

      renderHarness("/");
      emit(stream, [
        {
          entity_type: "reference_unit",
          action: "updated",
          entity_id: "E1U1",
          label: "视频单元「E1U1」",
          episode: 1,
          focus: null,
          important: false,
        },
        taskChange({ task_type: "reference_video" }),
      ]);

      expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(1);
    });

    it("参考生视频任务失败/取消不重拉分组（成片未变）", async () => {
      const stream = mockProjectEventStream();
      vi.spyOn(useTasksStore.getState(), "refreshTasks").mockResolvedValue(undefined);

      renderHarness("/");
      emit(stream, [
        taskChange({ action: "task_failed", task_type: "reference_video" }),
        taskChange({ entity_id: "task-2", action: "task_cancelled", task_type: "reference_video" }),
      ]);

      expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(0);
    });

    it.each(["task_failed" as const, "task_cancelled" as const])(
      "voice_sample 任务落 %s 终态时仍刷新成本（合成成功后计费，校验/取消发生在计费之后）",
      async (action) => {
        const stream = mockProjectEventStream();
        vi.spyOn(useTasksStore.getState(), "refreshTasks").mockResolvedValue(undefined);
        const debouncedFetchSpy = vi.spyOn(useCostStore.getState(), "debouncedFetch");

        renderHarness("/");
        emit(stream, [taskChange({ action, task_type: "voice_sample" })]);

        expect(debouncedFetchSpy).toHaveBeenCalledWith("demo");
      },
    );

    it("其它类型任务失败/取消不触发成本刷新（未计费或已由 voice_sample_ready 覆盖）", async () => {
      const stream = mockProjectEventStream();
      vi.spyOn(useTasksStore.getState(), "refreshTasks").mockResolvedValue(undefined);
      const debouncedFetchSpy = vi.spyOn(useCostStore.getState(), "debouncedFetch");

      renderHarness("/");
      emit(stream, [
        taskChange({ action: "task_failed", task_type: "video" }),
        taskChange({ entity_id: "task-2", action: "task_cancelled", task_type: "storyboard" }),
      ]);

      expect(debouncedFetchSpy).not.toHaveBeenCalled();
    });

    it("其它类型任务成功不触发参考生视频画布重拉", async () => {
      const stream = mockProjectEventStream();
      vi.spyOn(useTasksStore.getState(), "refreshTasks").mockResolvedValue(undefined);

      renderHarness("/");
      emit(stream, [taskChange({ task_type: "video" })]);

      expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(0);
    });

    it("任务终态不写入实体版本表（entity_id 是一次性 task_id，无人消费）", async () => {
      const stream = mockProjectEventStream();
      vi.spyOn(useTasksStore.getState(), "refreshTasks").mockResolvedValue(undefined);

      renderHarness("/");
      emit(stream, [
        taskChange(),
        taskChange({ entity_id: "task-2", action: "task_failed" }),
      ]);

      expect(Object.keys(useAppStore.getState().entityRevisions)).toHaveLength(0);
    });

    it("任务终态到达不清掉前一批实体变更排队中的聚焦目标", async () => {
      const stream = mockProjectEventStream();
      vi.spyOn(useTasksStore.getState(), "refreshTasks").mockResolvedValue(undefined);
      const d1 = createDeferred<GetWorkspaceResult>();
      vi.spyOn(API, "getWorkspace").mockReturnValue(d1.promise);

      renderHarness("/");

      act(() => {
        stream.options?.onChanges?.(
          {
            project_name: "demo",
            batch_id: "batch-entity",
            fingerprint: "fp-entity",
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
        );
      });

      emit(stream, [taskChange()]);

      await act(async () => {
        d1.resolve(makeGetWorkspaceResult("R1"));
        await Promise.resolve();
        await Promise.resolve();
      });

      await waitFor(() => {
        expect(screen.getByTestId("location")).toHaveTextContent("/characters");
      });
    });
  });

  describe("记账结算事件", () => {
    function usageRecordChange(overrides: Partial<ProjectChange> = {}): ProjectChange {
      return {
        entity_type: "usage_record",
        action: "recorded",
        entity_id: "1",
        label: "1",
        focus: null,
        important: false,
        status: "success",
        ...overrides,
      };
    }

    function emitUsage(
      stream: ReturnType<typeof mockProjectEventStream>,
      changes: ProjectChange[],
    ) {
      act(() => {
        stream.options?.onChanges?.(
          {
            project_name: "demo",
            batch_id: "batch-usage",
            fingerprint: "fp-usage",
            generated_at: "2026-03-01T00:00:00Z",
            source: "worker",
            changes,
          },
        );
      });
    }

    it("结算事件不写实体版本表（entity_id 是一次性调用 id，无人消费）", () => {
      const stream = mockProjectEventStream();

      renderHarness("/");
      emitUsage(stream, [usageRecordChange(), usageRecordChange({ entity_id: "2", status: "failed" })]);

      expect(Object.keys(useAppStore.getState().entityRevisions)).toHaveLength(0);
    });

    it("仅含结算事件的批次也刷新成本（无任务的调用只有这一个费用变动信号）", () => {
      const stream = mockProjectEventStream();
      const debouncedFetchSpy = vi.spyOn(useCostStore.getState(), "debouncedFetch");

      renderHarness("/");
      emitUsage(stream, [usageRecordChange({ status: "failed" })]);

      expect(debouncedFetchSpy).toHaveBeenCalledWith("demo");
    });

    it("结算事件不弹通知、不触发聚焦跳转（important=false / focus=null）", () => {
      const stream = mockProjectEventStream();

      renderHarness("/");
      emitUsage(stream, [usageRecordChange()]);

      expect(useAppStore.getState().toast).toBeNull();
      expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);
      expect(screen.getByTestId("location")).toHaveTextContent("/");
    });
  });

  describe("按集刷新、位置事实与前后台收口", () => {
    it("第 N 集分镜变更在未打开该集时不拉剧本，并用事件自带 position 渲染通知", async () => {
      const stream = mockProjectEventStream();
      const getScriptSpy = vi.spyOn(API, "getScript");

      renderHarness("/");
      act(() => {
        stream.options?.onChanges?.({
          project_name: "demo",
          batch_id: "batch-ep1-pos",
          fingerprint: "fp-ep1-pos",
          generated_at: "2026-03-01T00:00:00Z",
          source: "worker",
          changes: [
            {
              entity_type: "segment",
              action: "storyboard_ready",
              entity_id: "E1S02",
              item_id: "E1S02",
              episode: 1,
              position: 2,
              label: "分镜「E1S02」",
              label_key: "skeleton_segments",
              label_params: { id: "E1S02" },
              focus: null,
              important: true,
            },
          ],
        });
      });

      await waitFor(() => {
        expect(useAppStore.getState().toast?.text).toContain("第一集 · 2");
      });
      expect(getScriptSpy).not.toHaveBeenCalled();
    });

    it("后台标签页收到变更后延后刷新，切回前台时按待刷作用域补取一次", async () => {
      const originalVisibility = Object.getOwnPropertyDescriptor(document, "visibilityState");
      let visibility: DocumentVisibilityState = "hidden";
      Object.defineProperty(document, "visibilityState", {
        configurable: true,
        get: () => visibility,
      });
      try {
        const stream = mockProjectEventStream();
        renderHarness("/");

        act(() => {
          stream.options?.onChanges?.({
            project_name: "demo",
            batch_id: "batch-bg",
            fingerprint: "fp-bg",
            generated_at: "2026-03-01T00:00:00Z",
            source: "filesystem",
            changes: [
              {
                entity_type: "character",
                action: "updated",
                entity_id: "hero",
                label: "角色「hero」",
                focus: null,
                important: false,
              },
            ],
          });
        });

        expect(API.getWorkspace).not.toHaveBeenCalled();

        visibility = "visible";
        act(() => {
          document.dispatchEvent(new Event("visibilitychange"));
        });

        await waitFor(() => {
          expect(API.getWorkspace).toHaveBeenCalledTimes(1);
        });
      } finally {
        if (originalVisibility) {
          Object.defineProperty(document, "visibilityState", originalVisibility);
        } else {
          delete (document as { visibilityState?: DocumentVisibilityState }).visibilityState;
        }
      }
    });

    it("SSE snapshot 到来后唤醒 item-locations 中处于 unloaded 的条目重查", async () => {
      const stream = mockProjectEventStream();
      const locateSpy = vi
        .spyOn(API, "locateItems")
        .mockResolvedValueOnce({
          items: [{ episode: 2, item_id: "E2S01", position: null, status: "unloaded" }],
        })
        .mockResolvedValueOnce({
          items: [{ episode: 2, item_id: "E2S01", position: 1, status: "found" }],
        });

      renderHarness("/");
      const release = useItemLocationsStore.getState().registerVisible("demo", ["E2S01"]);
      await waitFor(() => expect(locateSpy).toHaveBeenCalledTimes(1));
      expect(useItemLocationsStore.getState().getLocation("demo", "E2S01")?.status).toBe("unloaded");

      act(() => {
        stream.options?.onSnapshot?.({
          project_name: "demo",
          fingerprint: "fp-snap-1",
          generated_at: "2026-03-01T00:00:00Z",
        });
      });

      await waitFor(() => {
        expect(locateSpy).toHaveBeenCalledTimes(2);
        expect(useItemLocationsStore.getState().getLocation("demo", "E2S01")).toEqual({
          episode: 2,
          item_id: "E2S01",
          position: 1,
          status: "found",
        });
      });
      release();
    });
  });
});
