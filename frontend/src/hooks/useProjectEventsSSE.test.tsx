import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router, useLocation } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API, type ProjectEventStreamOptions } from "@/api";
import { LeaveGuardProvider, useLeaveGuard } from "@/components/shared/edit-unit/LeaveGuard";
import { useProjectEventsSSE } from "./useProjectEventsSSE";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useCostStore } from "@/stores/cost-store";
import { useTasksStore } from "@/stores/tasks-store";
import type { ProjectChange } from "@/types";
import { createDeferred } from "@/test/deferred";
import { FakeSseStream } from "@/test/fakeSseStream";

/**
 * Stub `API.openProjectEventStream` as spies returning {@link FakeSseStream} handles:
 * tests directly drive `options` callbacks (onSnapshot/onChanges/onProjectDeleted),
 * without real fetch streams. `options` reflects latest hook-registered callbacks.
 */
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
    /** Latest established connection; use its `close` to assert close counts. */
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

type GetProjectResult = Awaited<ReturnType<typeof API.getProject>>;

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
          novel: { title: "", chapter: "" },
          segments: [],
        },
      },
    });
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
              // label is backend-default-language fallback; notification copy must render label_key in UI languages.
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
      expect(API.getProject).toHaveBeenCalledWith("demo", { signal: expect.any(AbortSignal) });
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
      expect(API.getProject).toHaveBeenCalledWith("demo", { signal: expect.any(AbortSignal) });
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
      expect(API.getProject).toHaveBeenCalledWith("demo", { signal: expect.any(AbortSignal) });
      expect(useAppStore.getState().workspaceNotifications[0]?.target?.id).toBe("酒馆");
    });
    expect(screen.getByTestId("location")).toHaveTextContent("/");
    expect(useAppStore.getState().scrollTarget).toBeNull();
  });

  it("invalidates episode draft views when a reconnect snapshot shows missed changes", () => {
    // Draft panels fetch independently, refetching only on draft events; invalidate missed disconnected-period changes on reconnect snapshots.
    useProjectsStore.setState({
      currentProjectData: { ...makeGetProjectResult("Demo").project, episodes: [{ episode: 4, title: "雨夜" }] },
    } as never);
    const stream = mockProjectEventStream();
    renderHarness("/");
    const revision = () => useAppStore.getState().getEntityRevision("draft:episode_4_script_plan");

    act(() => stream.options?.onSnapshot?.({ project_name: "demo", fingerprint: "fp-a" } as never));
    const before = revision();
    act(() => stream.options?.onSnapshot?.({ project_name: "demo", fingerprint: "fp-b" } as never));

    expect(revision()).toBeGreaterThan(before);
    expect(useAppStore.getState().getEntityRevision("draft:episode_4_prompt_authoring")).toBeGreaterThan(0);
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
    // Recovery notices push once only: events before subscriptions (initial-connect retries/disconnected gaps) require snapshot reconciliation.
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
    // Local ledgers lack this episode when events arrive; wait for refresh before title-based notices, never show unnamed episodes.
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
    // New storyboards are absent from old scripts; failed refreshes would label them deleted, so retain synchronization warnings only.
    vi.spyOn(API, "getProject").mockRejectedValue(new Error("network down"));
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
      expect(API.getProject).toHaveBeenCalledWith("demo", { signal: expect.any(AbortSignal) });
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
        expect(API.getProject).toHaveBeenCalledWith("demo", { signal: expect.any(AbortSignal) });
        expect(useAppStore.getState().toast?.text).toBe(expectedText);
      });
      expect(useAppStore.getState().toast?.tone).toBe("success");
      expect(screen.getByTestId("location")).toHaveTextContent("/episodes/1");
      expect(useAppStore.getState().scrollTarget).toBeNull();
      expect(debouncedFetchSpy).toHaveBeenCalledWith("demo");
    },
  );

  it("ranks reference_video_ready/tts_ready alongside existing completion events, above entity changes", async () => {
    // CHANGE_PRIORITY places reference_video_ready/tts_ready after storyboard_ready/video_ready/grid_ready
    // groups. Multiple same-batch groups overwrite toast state; final notices must reflect highest numeric priority (processed last).
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
      expect(API.getProject).toHaveBeenCalledWith("demo", { signal: expect.any(AbortSignal) });
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
      expect(API.getProject).toHaveBeenCalledWith("demo", { signal: expect.any(AbortSignal) });
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
      expect(API.getProject).toHaveBeenCalledWith("demo", { signal: expect.any(AbortSignal) });
    });
    expect(screen.getByTestId("location")).toHaveTextContent("/props");
    expect(useAppStore.getState().scrollTarget).toBeNull();
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);
  });

  // Draft creation alone auto-navigates: Agent-written new drafts open matching episodes; user Web saves stay on current pages.
  function emitDraftCreated(stream: ReturnType<typeof mockProjectEventStream>, source: "webui" | "worker") {
    act(() => {
      stream.options?.onChanges?.({
        project_name: "demo",
        batch_id: `batch-draft-${source}`,
        fingerprint: `fp-draft-${source}`,
        generated_at: "2026-03-01T00:00:00Z",
        source,
        changes: [
          {
            entity_type: "draft",
            action: "created",
            entity_id: "episode_1_script_plan",
            label: "第 1 集的剧本",
            label_key: "draft_normalized_script",
            label_params: { episode: 1 },
            episode: 1,
            focus: { pane: "episode", episode: 1 },
            important: true,
          },
        ],
      });
    });
  }

  it("navigates to the episode when another writer creates a draft", async () => {
    const stream = mockProjectEventStream();
    renderHarness("/characters");

    emitDraftCreated(stream, "worker");

    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/episodes/1");
    });
    await waitFor(() => {
      expect(useAppStore.getState().toast).not.toBeNull();
    });
  });

  it("stays put without notifications when the user's own draft save creates a draft", async () => {
    const stream = mockProjectEventStream();
    renderHarness("/characters");

    emitDraftCreated(stream, "webui");

    await waitFor(() => {
      expect(API.getProject).toHaveBeenCalledWith("demo", { signal: expect.any(AbortSignal) });
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByTestId("location")).toHaveTextContent("/characters");
    expect(useAppStore.getState().toast).toBeNull();
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
    // onSnapshot refreshProject() sets no new focus target; old settlement unconditionally
    // flushed queued focus. While in flight, real-target onChanges can queue,
    // replacing queuedFocusRef while data waits for its own getProject round. Unconditional snapshot
    // consumption navigates before target persistence and clears refs,
    // preventing later onChanges navigation.
    const stream = mockProjectEventStream();

    const d1 = createDeferred<GetProjectResult>();
    const d2 = createDeferred<GetProjectResult>();
    const getProjectSpy = vi
      .spyOn(API, "getProject")
      .mockReturnValueOnce(d1.promise)
      .mockReturnValueOnce(d2.promise);

    renderHarness("/");

    // Establish initial fingerprint baselines; first onSnapshot never refreshes.
    act(() => {
      stream.options?.onSnapshot?.(
        { project_name: "demo", fingerprint: "fp-a", generated_at: "2026-03-01T00:00:00Z" },
      );
    });
    expect(getProjectSpy).not.toHaveBeenCalled();

    // Changed fingerprints start targetless refreshes, with getProject/d1 pending.
    act(() => {
      stream.options?.onSnapshot?.(
        { project_name: "demo", fingerprint: "fp-b", generated_at: "2026-03-01T00:00:01Z" },
      );
    });
    expect(getProjectSpy).toHaveBeenCalledTimes(1);

    // Real-target onChanges arrives/queues while in flight.
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
    // Merge queues only, without immediate extra requests.
    expect(getProjectSpy).toHaveBeenCalledTimes(1);

    // Snapshot round settles without early navigation to queued /characters.
    await act(async () => {
      d1.resolve(makeGetProjectResult("R1"));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByTestId("location")).toHaveTextContent("/");

    // onChanges round settles, navigating true targets with exactly one follow-up.
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
    // Reproduction: overlapping onChanges; first refreshProject/getProject still pending when
    // second replaces queuedFocusRef. First settlement must not consume superseded
    // refs for early navigation; wait for second rounds before their target navigation.
    const stream = mockProjectEventStream();

    const d1 = createDeferred<GetProjectResult>();
    const d2 = createDeferred<GetProjectResult>();
    const getProjectSpy = vi
      .spyOn(API, "getProject")
      .mockReturnValueOnce(d1.promise)
      .mockReturnValueOnce(d2.promise);

    renderHarness("/");

    // First: hero → /characters, getProject/d1 pending.
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

    // Second arrives during first: 酒馆 scene → /scenes, replacing queuedFocusRef.
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
    // In-flight merging queues second calls without immediate requests.
    expect(getProjectSpy).toHaveBeenCalledTimes(1);

    // Initial settlement must not navigate early to superseded /characters.
    await act(async () => {
      d1.resolve(makeGetProjectResult("R1"));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByTestId("location")).toHaveTextContent("/");

    // Queued second settlement navigates /scenes, with one follow-up request only.
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

    // Fingerprints write synchronously before getProject.
    expect(useProjectsStore.getState().getAssetFingerprint("storyboards/scene_E1S01.png")).toBe(1710288000);
  });

  it("closes the stream handle after the project_deleted termination event", () => {
    const stream = mockProjectEventStream();

    renderHarness("/");
    expect(stream.openSpy).toHaveBeenCalledTimes(1);

    // Deleted projects normally close server streams; clients own reconnects, and hook handle closure stops them.
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
      const getProjectSpy = vi.spyOn(API, "getProject");

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
        expect(API.getProject).toHaveBeenCalledWith("demo", { signal: expect.any(AbortSignal) });
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
      // Every terminal entity_id is new task_id; entity invalidation would leave
      // unconsumed keys never cleared across projects, growing unbounded in long sessions.
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
      // Task-terminal batches arrive while entity refreshProject remains pending. Targetless task
      // changes entering generic focus handling overwrite queued targets null, losing intended navigation.
      const stream = mockProjectEventStream();
      vi.spyOn(useTasksStore.getState(), "refreshTasks").mockResolvedValue(undefined);
      const d1 = createDeferred<GetProjectResult>();
      vi.spyOn(API, "getProject").mockReturnValue(d1.promise);

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
        d1.resolve(makeGetProjectResult("R1"));
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
});
