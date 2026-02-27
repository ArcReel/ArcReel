import { beforeEach, describe, expect, it, vi } from "vitest";
import { API, type TaskStreamOptions } from "@/api";
import type { TaskItem } from "@/types";

type JsonResponseOptions = {
  ok?: boolean;
  statusText?: string;
  jsonData?: unknown;
  jsonError?: Error;
  textData?: string;
};

function mockResponse(options: JsonResponseOptions = {}): Response {
  const {
    ok = true,
    statusText = "OK",
    jsonData = {},
    jsonError,
    textData = "",
  } = options;

  return {
    ok,
    statusText,
    json: jsonError
      ? vi.fn().mockRejectedValue(jsonError)
      : vi.fn().mockResolvedValue(jsonData),
    text: vi.fn().mockResolvedValue(textData),
  } as unknown as Response;
}

function makeTask(overrides: Partial<TaskItem> = {}): TaskItem {
  return {
    task_id: "task-1",
    project_name: "demo",
    task_type: "storyboard",
    media_type: "image",
    resource_id: "segment-1",
    script_file: null,
    payload: {},
    status: "queued",
    result: null,
    error_message: null,
    source: "webui",
    queued_at: "2026-02-01T00:00:00Z",
    started_at: null,
    finished_at: null,
    updated_at: "2026-02-01T00:00:00Z",
    ...overrides,
  };
}

class MockEventSource {
  onerror: ((event: Event) => void) | null = null;
  close = vi.fn();
  private readonly listeners = new Map<string, Array<(event: Event) => void>>();

  constructor(public readonly url: string) {}

  addEventListener(type: string, cb: (event: Event) => void): void {
    const list = this.listeners.get(type) ?? [];
    list.push(cb);
    this.listeners.set(type, list);
  }

  emit(type: string, data: string): void {
    const event = { data } as MessageEvent;
    const listeners = this.listeners.get(type) ?? [];
    for (const listener of listeners) {
      listener(event as unknown as Event);
    }
  }
}

describe("API", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  describe("request", () => {
    it("returns parsed JSON and applies default JSON header", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        mockResponse({ jsonData: { ok: true } }),
      );
      vi.stubGlobal("fetch", fetchMock);

      const result = await API.request("/projects");

      expect(result).toEqual({ ok: true });
      expect(fetchMock).toHaveBeenCalledWith("/api/v1/projects", {
        headers: { "Content-Type": "application/json" },
      });
    });

    it("throws backend detail for failed request", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        mockResponse({
          ok: false,
          jsonData: { detail: "boom" },
          statusText: "Bad Request",
        }),
      );
      vi.stubGlobal("fetch", fetchMock);

      await expect(API.request("/projects")).rejects.toThrow("boom");
    });

    it("falls back to statusText when error response is not JSON", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        mockResponse({
          ok: false,
          statusText: "Service Unavailable",
          jsonError: new Error("not json"),
        }),
      );
      vi.stubGlobal("fetch", fetchMock);

      await expect(API.request("/projects")).rejects.toThrow("Service Unavailable");
    });
  });

  describe("request-based wrappers", () => {
    it("covers project, character, clue, script and generation endpoints", async () => {
      const requestSpy = vi
        .spyOn(API, "request")
        .mockResolvedValue({ success: true } as never);

      await API.listProjects();
      await API.createProject("demo", "Demo");
      await API.getProject("a b");
      await API.updateProject("demo", { style: "Anime" });
      await API.deleteProject("demo");

      await API.addCharacter("demo", "Hero", "brave");
      await API.updateCharacter("demo", "Hero", { description: "updated" });
      await API.deleteCharacter("demo", "Hero");

      await API.addClue("demo", "Key", "prop", "important");
      await API.updateClue("demo", "Key", { importance: "minor" });
      await API.deleteClue("demo", "Key");

      await API.getScript("demo", "episode 1.json");
      await API.updateScene("demo", "scene-1", "episode_1.json", { x: 1 });
      await API.updateSegment("demo", "segment-1", { y: 2 });

      await API.listFiles("demo");
      await API.listDrafts("demo");
      await API.deleteDraft("demo", 1, 2);
      await API.generateOverview("demo");
      await API.updateOverview("demo", { synopsis: "new" });

      await API.generateStoryboard("demo", "seg-1", "img", "episode_1.json");
      await API.generateVideo("demo", "seg-1", "vid", "episode_1.json");
      await API.generateCharacter("demo", "Hero", "prompt");
      await API.generateClue("demo", "Key", "prompt");

      expect(requestSpy).toHaveBeenCalledWith("/projects");
      expect(requestSpy).toHaveBeenCalledWith("/projects", {
        method: "POST",
        body: JSON.stringify({
          name: "demo",
          title: "Demo",
          style: "",
          content_mode: "narration",
        }),
      });
      expect(requestSpy).toHaveBeenCalledWith("/projects/a%20b");
      expect(requestSpy).toHaveBeenCalledWith("/projects/demo", {
        method: "PATCH",
        body: JSON.stringify({ style: "Anime" }),
      });
      expect(requestSpy).toHaveBeenCalledWith("/projects/demo", {
        method: "DELETE",
      });
      expect(requestSpy).toHaveBeenCalledWith("/projects/demo/characters", {
        method: "POST",
        body: JSON.stringify({
          name: "Hero",
          description: "brave",
          voice_style: "",
        }),
      });
      expect(requestSpy).toHaveBeenCalledWith("/projects/demo/clues", {
        method: "POST",
        body: JSON.stringify({
          name: "Key",
          clue_type: "prop",
          description: "important",
          importance: "major",
        }),
      });
      expect(requestSpy).toHaveBeenCalledWith(
        "/projects/demo/scripts/episode%201.json",
      );
      expect(requestSpy).toHaveBeenCalledWith("/projects/demo/scenes/scene-1", {
        method: "PATCH",
        body: JSON.stringify({ script_file: "episode_1.json", updates: { x: 1 } }),
      });
      expect(requestSpy).toHaveBeenCalledWith("/projects/demo/segments/segment-1", {
        method: "PATCH",
        body: JSON.stringify({ y: 2 }),
      });
      expect(requestSpy).toHaveBeenCalledWith("/projects/demo/generate/video/seg-1", {
        method: "POST",
        body: JSON.stringify({
          prompt: "vid",
          script_file: "episode_1.json",
          duration_seconds: 4,
        }),
      });
    });

    it("covers task, assistant, version and usage query builders", async () => {
      const requestSpy = vi
        .spyOn(API, "request")
        .mockResolvedValue({ success: true } as never);

      await API.getTask("task id");
      await API.listTasks({
        projectName: "demo",
        status: "running",
        taskType: "video",
        source: "webui",
        page: 2,
        pageSize: 10,
      });
      await API.listProjectTasks("demo", {
        status: "failed",
        taskType: "image",
        source: "agent",
        page: 3,
        pageSize: 20,
      });
      await API.getTaskStats("demo");
      await API.getVersions("demo", "storyboards", "seg-1");
      await API.restoreVersion("demo", "storyboards", "seg-1", 3);
      await API.deleteStyleImage("demo");
      await API.updateStyleDescription("demo", "moody");

      await API.createAssistantSession("demo", "My Session");
      await API.listAssistantSessions("demo", "running");
      await API.getAssistantSession("session-1");
      await API.getAssistantSnapshot("session-1");
      await API.sendAssistantMessage("session-1", "hello");
      await API.interruptAssistantSession("session-1");
      await API.answerAssistantQuestion("session-1", "q-1", { key: ["a"] });
      await API.listAssistantSkills("demo");
      await API.updateAssistantSession("session-1", { title: "Renamed", status: "idle" });
      await API.deleteAssistantSession("session-1");

      await API.getUsageStats({
        projectName: "demo",
        startDate: "2026-01-01",
        endDate: "2026-02-01",
      });
      await API.getUsageCalls({
        projectName: "demo",
        callType: "image",
        status: "succeeded",
        startDate: "2026-01-01",
        endDate: "2026-02-01",
        page: 1,
        pageSize: 50,
      });
      await API.getUsageProjects();

      expect(requestSpy).toHaveBeenCalledWith("/tasks/task%20id");
      expect(requestSpy).toHaveBeenCalledWith(
        "/tasks?project_name=demo&status=running&task_type=video&source=webui&page=2&page_size=10",
      );
      expect(requestSpy).toHaveBeenCalledWith(
        "/projects/demo/tasks?status=failed&task_type=image&source=agent&page=3&page_size=20",
      );
      expect(requestSpy).toHaveBeenCalledWith("/tasks/stats?project_name=demo");
      expect(requestSpy).toHaveBeenCalledWith(
        "/assistant/sessions?project_name=demo&status=running",
      );
      expect(requestSpy).toHaveBeenCalledWith("/assistant/skills?project_name=demo");
      expect(requestSpy).toHaveBeenCalledWith(
        "/usage/stats?project_name=demo&start_date=2026-01-01&end_date=2026-02-01",
      );
      expect(requestSpy).toHaveBeenCalledWith(
        "/usage/calls?project_name=demo&call_type=image&status=succeeded&start_date=2026-01-01&end_date=2026-02-01&page=1&page_size=50",
      );
      expect(requestSpy).toHaveBeenCalledWith("/usage/projects");
    });

    it("builds static file and stream urls", () => {
      expect(API.getFileUrl("my project", "source/a.txt")).toBe(
        "/api/v1/files/my%20project/source/a.txt",
      );
      expect(API.getAssistantStreamUrl("session-1")).toBe(
        "/api/v1/assistant/sessions/session-1/stream",
      );
    });
  });

  describe("fetch-based wrappers", () => {
    it("uploads files via multipart form and returns JSON", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        mockResponse({ jsonData: { success: true, path: "p", url: "u" } }),
      );
      vi.stubGlobal("fetch", fetchMock);

      const file = new File(["hello"], "demo.txt", { type: "text/plain" });
      const result = await API.uploadFile("my project", "source", file, "x y");

      expect(result).toEqual({ success: true, path: "p", url: "u" });
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(fetchMock.mock.calls[0][0]).toBe(
        "/api/v1/projects/my%20project/upload/source?name=x%20y",
      );
      expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
      expect((fetchMock.mock.calls[0][1] as RequestInit).body).toBeInstanceOf(FormData);
    });

    it("throws detail when upload fails", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        mockResponse({
          ok: false,
          statusText: "Bad Request",
          jsonData: { detail: "上传失败" },
        }),
      );
      vi.stubGlobal("fetch", fetchMock);
      const file = new File(["hello"], "demo.txt", { type: "text/plain" });

      await expect(API.uploadFile("demo", "source", file)).rejects.toThrow("上传失败");
    });

    it("handles source and draft text APIs", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValueOnce(mockResponse({ textData: "source content" }))
        .mockResolvedValueOnce(
          mockResponse({ jsonData: { success: true }, statusText: "OK" }),
        )
        .mockResolvedValueOnce(
          mockResponse({ jsonData: { success: true }, statusText: "OK" }),
        )
        .mockResolvedValueOnce(mockResponse({ textData: "draft content" }))
        .mockResolvedValueOnce(
          mockResponse({ jsonData: { success: true }, statusText: "OK" }),
        );
      vi.stubGlobal("fetch", fetchMock);

      await expect(API.getSourceContent("demo", "source.txt")).resolves.toBe(
        "source content",
      );
      await expect(API.saveSourceFile("demo", "source.txt", "hello")).resolves.toEqual({
        success: true,
      });
      await expect(API.deleteSourceFile("demo", "source.txt")).resolves.toEqual({
        success: true,
      });
      await expect(API.getDraftContent("demo", 1, 2)).resolves.toBe("draft content");
      await expect(API.saveDraft("demo", 1, 2, "draft")).resolves.toEqual({
        success: true,
      });

      expect(fetchMock).toHaveBeenNthCalledWith(
        2,
        "/api/v1/projects/demo/source/source.txt",
        {
          method: "PUT",
          headers: { "Content-Type": "text/plain" },
          body: "hello",
        },
      );
      expect(fetchMock).toHaveBeenNthCalledWith(
        3,
        "/api/v1/projects/demo/source/source.txt",
        { method: "DELETE" },
      );
    });

    it("falls back to status text in text endpoint errors", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        mockResponse({
          ok: false,
          statusText: "Not Found",
          jsonError: new Error("invalid json"),
        }),
      );
      vi.stubGlobal("fetch", fetchMock);

      await expect(API.getSourceContent("demo", "missing.txt")).rejects.toThrow(
        "Not Found",
      );
    });

    it("uploads style image using multipart form", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        mockResponse({
          jsonData: {
            success: true,
            style_image: "image.png",
            style_description: "style",
            url: "/x",
          },
        }),
      );
      vi.stubGlobal("fetch", fetchMock);
      const file = new File(["img"], "style.png", { type: "image/png" });

      const res = await API.uploadStyleImage("demo", file);
      expect(res.success).toBe(true);
      expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/projects/demo/style-image");
      expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
    });
  });

  describe("openTaskStream", () => {
    it("builds stream URL, dispatches events and forwards onError", () => {
      const instances: MockEventSource[] = [];
      class EventSourceMock extends MockEventSource {
        constructor(url: string) {
          super(url);
          instances.push(this);
        }
      }
      vi.stubGlobal("EventSource", EventSourceMock as unknown as typeof EventSource);

      const onSnapshot = vi.fn();
      const onTask = vi.fn();
      const onHeartbeat = vi.fn();
      const onError = vi.fn();
      const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

      const source = API.openTaskStream({
        projectName: "demo",
        lastEventId: "42",
        onSnapshot,
        onTask,
        onHeartbeat,
        onError,
      });

      expect(instances[0].url).toBe(
        "/api/v1/tasks/stream?project_name=demo&last_event_id=42",
      );

      const es = instances[0];
      es.emit(
        "snapshot",
        JSON.stringify({
          tasks: [makeTask()],
          stats: { queued: 1, running: 0, succeeded: 0, failed: 0, total: 1 },
        }),
      );
      es.emit(
        "task",
        JSON.stringify({
          action: "updated",
          task: makeTask({ status: "running" }),
          stats: { queued: 0, running: 1, succeeded: 0, failed: 0, total: 1 },
        }),
      );
      es.emit("heartbeat", JSON.stringify({ timestamp: "2026-02-02T00:00:00Z" }));
      es.emit("snapshot", "{invalid json");

      expect(onSnapshot).toHaveBeenCalledTimes(1);
      expect(onTask).toHaveBeenCalledTimes(1);
      expect(onHeartbeat).toHaveBeenCalledTimes(1);
      expect(consoleError).toHaveBeenCalled();

      const errEvent = new Event("error");
      es.onerror?.(errEvent);
      expect(onError).toHaveBeenCalledWith(errEvent);
      expect(source).toBe(es as unknown as EventSource);
    });

    it("ignores invalid lastEventId", () => {
      const instances: MockEventSource[] = [];
      class EventSourceMock extends MockEventSource {
        constructor(url: string) {
          super(url);
          instances.push(this);
        }
      }
      vi.stubGlobal("EventSource", EventSourceMock as unknown as typeof EventSource);

      API.openTaskStream({ projectName: "demo", lastEventId: "0" });
      expect(instances[0].url).toBe("/api/v1/tasks/stream?project_name=demo");
    });
  });
});
