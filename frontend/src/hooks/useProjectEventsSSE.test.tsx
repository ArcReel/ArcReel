import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router, useLocation } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API, type ProjectEventStreamOptions } from "@/api";
import { useProjectEventsSSE } from "./useProjectEventsSSE";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";

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
          summary: "",
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
});
