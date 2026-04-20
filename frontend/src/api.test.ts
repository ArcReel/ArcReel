import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";
import { API, ConflictError } from "@/api";
import type { TaskItem } from "@/types";

type JsonResponseOptions = {
  ok?: boolean;
  status?: number;
  statusText?: string;
  jsonData?: unknown;
  jsonError?: Error;
  textData?: string;
  blobData?: Blob;
  headers?: HeadersInit;
};

function mockResponse(options: JsonResponseOptions = {}): Response {
  const {
    ok = true,
    status = ok ? 200 : 400,
    statusText = "OK",
    jsonData = {},
    jsonError,
    textData = "",
    blobData = new Blob(),
    headers = {},
  } = options;

  return {
    ok,
    status,
    statusText,
    headers: new Headers(headers),
    json: jsonError
      ? vi.fn().mockRejectedValue(jsonError)
      : vi.fn().mockResolvedValue(jsonData),
    text: vi.fn().mockResolvedValue(textData),
    blob: vi.fn().mockResolvedValue(blobData),
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
    cancelled_by: null,
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
      expect(fetchMock).toHaveBeenCalledWith("/api/v1/projects", expect.objectContaining({
        headers: expect.any(Headers),
      }));
      const headers = fetchMock.mock.calls[0][1].headers as Headers;
      expect(headers.get("Content-Type")).toBe("application/json");
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

    it("clears auth and redirects on unauthorized responses", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        mockResponse({
          ok: false,
          status: 401,
          statusText: "Unauthorized",
        }),
      );
      vi.stubGlobal("fetch", fetchMock);
      const clearTokenMock = vi.spyOn(await import("@/utils/auth"), "clearToken");
      const location = { href: "/app" };
      vi.stubGlobal("location", location);

      await expect(API.request("/projects")).rejects.toThrow("认证已过期，请重新登录");

      expect(clearTokenMock).toHaveBeenCalledTimes(1);
      expect(location.href).toBe("/login");
    });
  });

  describe("request-based wrappers", () => {
    it("covers project, character, scene, prop, script and generation endpoints", async () => {
      const requestSpy = vi
        .spyOn(API, "request")
        .mockResolvedValue({ success: true } as never);

      await API.listProjects();
      await API.createProject({ title: "Demo" });
      await API.createProject({ title: "Untitled" });
      await API.getProject("a b");
      await API.updateProject("demo", { style: "Anime" });
      await API.deleteProject("demo");

      await API.addCharacter("demo", "Hero", "brave");
      await API.updateCharacter("demo", "Hero", { description: "updated" });
      await API.deleteCharacter("demo", "Hero");

      await API.addProjectScene("demo", "Temple", "ancient");
      await API.updateProjectScene("demo", "Temple", { description: "dark" });
      await API.deleteProjectScene("demo", "Temple");
      await API.addProjectProp("demo", "Sword", "rusty");
      await API.updateProjectProp("demo", "Sword", { description: "shiny" });
      await API.deleteProjectProp("demo", "Sword");

      await API.getScript("demo", "episode 1.json");
      await API.updateScene("demo", "scene-1", "episode_1.json", { x: 1 });
      await API.updateSegment("demo", "segment-1", { y: 2 });

      await API.getSystemConfig();
      await API.updateSystemConfig({ default_image_backend: "vertex" });
      await API.listFiles("demo");
      await API.listDrafts("demo");
      await API.deleteDraft("demo", 1, 2);
      await API.generateOverview("demo");
      await API.updateOverview("demo", { synopsis: "new" });

      await API.generateStoryboard("demo", "seg-1", "img", "episode_1.json");
      await API.generateVideo("demo", "seg-1", "vid", "episode_1.json");
      await API.generateCharacter("demo", "Hero", "prompt");
      await API.generateProjectScene("demo", "Temple", "prompt");
      await API.generateProjectProp("demo", "Sword", "prompt");

      expect(requestSpy).toHaveBeenCalledWith("/projects");
      expect(requestSpy).toHaveBeenCalledWith("/projects", {
        method: "POST",
        body: JSON.stringify({ title: "Demo" }),
      });
      expect(requestSpy).toHaveBeenCalledWith("/projects", {
        method: "POST",
        body: JSON.stringify({ title: "Untitled" }),
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
      expect(requestSpy).toHaveBeenCalledWith("/projects/demo/scenes", {
        method: "POST",
        body: JSON.stringify({ name: "Temple", description: "ancient" }),
      });
      expect(requestSpy).toHaveBeenCalledWith("/projects/demo/props", {
        method: "POST",
        body: JSON.stringify({ name: "Sword", description: "rusty" }),
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
      expect(requestSpy).toHaveBeenCalledWith("/system/config");
      expect(requestSpy).toHaveBeenCalledWith("/system/config", {
        method: "PATCH",
        body: JSON.stringify({ default_image_backend: "vertex" }),
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

    it("rejects unsupported project mode updates before sending the request", async () => {
      const requestSpy = vi
        .spyOn(API, "request")
        .mockResolvedValue({ success: true } as never);

      await expect(
        API.updateProject("demo", { content_mode: "drama" } as never),
      ).rejects.toThrow("项目创建后不支持修改 content_mode");
      expect(requestSpy).not.toHaveBeenCalled();
    });

    it("allows aspect_ratio updates via updateProject", async () => {
      const requestSpy = vi
        .spyOn(API, "request")
        .mockResolvedValue({ success: true } as never);

      await expect(
        API.updateProject("demo", { aspect_ratio: "16:9" }),
      ).resolves.not.toThrow();
      expect(requestSpy).toHaveBeenCalledOnce();
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

      await API.listAssistantSessions("demo", "running");
      await API.getAssistantSession("demo", "session-1");
      await API.getAssistantSnapshot("demo", "session-1");
      await API.sendAssistantMessage("demo", "hello", "session-1");
      await API.interruptAssistantSession("demo", "session-1");
      await API.answerAssistantQuestion("demo", "session-1", "q-1", { key: "a" });
      await API.listAssistantSkills("demo");
      await API.deleteAssistantSession("demo", "session-1");

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
        "/projects/demo/assistant/sessions?status=running",
      );
      expect(requestSpy).toHaveBeenCalledWith("/projects/demo/assistant/skills");
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
      expect(API.getFileUrl("my project", "source/a.txt", 3)).toBe(
        "/api/v1/files/my%20project/source/a.txt?v=3",
      );
      expect(API.getAssistantStreamUrl("demo", "session-1")).toBe(
        "/api/v1/projects/demo/assistant/sessions/session-1/stream",
      );
    });

    it("createProject sends object body with style_template_id and model fields", async () => {
      const requestSpy = vi.spyOn(API, "request").mockResolvedValue({ success: true } as never);
      await API.createProject({
        title: "P1",
        style_template_id: "live_premium_drama",
        content_mode: "drama",
        aspect_ratio: "9:16",
        video_backend: "gemini-aistudio/veo-3",
        default_duration: 8,
      });
      expect(requestSpy).toHaveBeenCalledWith("/projects", {
        method: "POST",
        body: JSON.stringify({
          title: "P1",
          style_template_id: "live_premium_drama",
          content_mode: "drama",
          aspect_ratio: "9:16",
          video_backend: "gemini-aistudio/veo-3",
          default_duration: 8,
        }),
      });
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
        expect.objectContaining({
          method: "PUT",
          body: "hello",
          headers: expect.any(Headers),
        }),
      );
      expect(fetchMock).toHaveBeenNthCalledWith(
        3,
        "/api/v1/projects/demo/source/source.txt",
        expect.objectContaining({ method: "DELETE" }),
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

    it("imports project via multipart form and preserves structured errors", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValueOnce(
          mockResponse({
            jsonData: {
              success: true,
              project_name: "demo",
              project: {
                title: "Demo",
                content_mode: "narration",
                style: "Anime",
                episodes: [],
                characters: {},
                scenes: {},
                props: {},
              },
              warnings: [],
              conflict_resolution: "none",
              diagnostics: {
                auto_fixed: [],
                warnings: [],
              },
            },
          }),
        )
        .mockResolvedValueOnce(
          mockResponse({
            ok: false,
            statusText: "Bad Request",
            jsonData: {
              detail: "导入包校验失败",
              errors: ["缺少 project.json", "缺少 scripts/episode_1.json"],
              warnings: ["发现未识别的附加文件/目录: extra"],
              diagnostics: {
                blocking: [
                  { code: "validation_error", message: "缺少 project.json" },
                  { code: "validation_error", message: "缺少 scripts/episode_1.json" },
                ],
                auto_fixable: [
                  { code: "missing_clues_field", message: "segments[0]: 补全缺失字段 clues_in_segment" },
                ],
                warnings: [
                  { code: "validation_warning", message: "发现未识别的附加文件/目录: extra" },
                ],
              },
            },
          }),
        );
      vi.stubGlobal("fetch", fetchMock);

      const file = new File(["zip"], "demo.zip", { type: "application/zip" });
      const result = await API.importProject(file, "overwrite");
      expect(result.project_name).toBe("demo");

      await expect(API.importProject(file)).rejects.toMatchObject({
        message: "导入包校验失败",
        detail: "导入包校验失败",
        errors: ["缺少 project.json", "缺少 scripts/episode_1.json"],
        warnings: ["发现未识别的附加文件/目录: extra"],
        diagnostics: {
          blocking: [
            { code: "validation_error", message: "缺少 project.json" },
            { code: "validation_error", message: "缺少 scripts/episode_1.json" },
          ],
          auto_fixable: [
            { code: "missing_clues_field", message: "segments[0]: 补全缺失字段 clues_in_segment" },
          ],
          warnings: [
            { code: "validation_warning", message: "发现未识别的附加文件/目录: extra" },
          ],
        },
      });

      expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/projects/import");
      expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
      expect((fetchMock.mock.calls[0][1] as RequestInit).body).toBeInstanceOf(FormData);
    });

    it("preserves conflict metadata for secondary confirmation", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        mockResponse({
          ok: false,
          status: 409,
          statusText: "Conflict",
          jsonData: {
            detail: "检测到项目编号冲突",
            errors: ["项目编号 'demo' 已存在"],
            warnings: [],
            conflict_project_name: "demo",
            diagnostics: {
              blocking: [],
              auto_fixable: [],
              warnings: [],
            },
          },
        }),
      );
      vi.stubGlobal("fetch", fetchMock);

      await expect(
        API.importProject(new File(["zip"], "demo.zip", { type: "application/zip" }))
      ).rejects.toMatchObject({
        message: "检测到项目编号冲突",
        status: 409,
        conflict_project_name: "demo",
      });
    });

    it("reuses unauthorized handling for import requests", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        mockResponse({
          ok: false,
          status: 401,
          statusText: "Unauthorized",
        }),
      );
      vi.stubGlobal("fetch", fetchMock);
      const clearTokenMock = vi.spyOn(await import("@/utils/auth"), "clearToken");
      const location = { href: "/app/projects" };
      vi.stubGlobal("location", location);

      await expect(
        API.importProject(new File(["zip"], "demo.zip", { type: "application/zip" }))
      ).rejects.toThrow("认证已过期，请重新登录");

      expect(clearTokenMock).toHaveBeenCalledTimes(1);
      expect(location.href).toBe("/login");
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
      const onError = vi.fn();
      const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

      const source = API.openTaskStream({
        projectName: "demo",
        lastEventId: "42",
        onSnapshot,
        onTask,
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
      es.emit("snapshot", "{invalid json");

      expect(onSnapshot).toHaveBeenCalledTimes(1);
      expect(onTask).toHaveBeenCalledTimes(1);
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

  describe("listAssets", () => {
    it("GETs /api/v1/assets with type query", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        mockResponse({ jsonData: { items: [] } }),
      );
      vi.stubGlobal("fetch", fetchMock);
      await API.listAssets({ type: "character" });
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/v1/assets"),
        expect.anything()
      );
    });
  });

  describe("createAsset", () => {
    it("POSTs multipart to /api/v1/assets", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        mockResponse({ jsonData: { asset: { id: "x", type: "scene", name: "A", description: "", voice_style: "", image_path: null, source_project: null, updated_at: null } } }),
      );
      vi.stubGlobal("fetch", fetchMock);
      const res = await API.createAsset({ type: "scene", name: "A", description: "d" });
      expect(res.asset.id).toBe("x");
    });
  });

  describe("addAssetFromProject", () => {
    it("POSTs /api/v1/assets/from-project", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        mockResponse({ jsonData: { asset: { id: "x", type: "character", name: "王", description: "", voice_style: "", image_path: null, source_project: "demo", updated_at: null } } }),
      );
      vi.stubGlobal("fetch", fetchMock);
      await API.addAssetFromProject({ project_name: "demo", resource_type: "character", resource_id: "王" });
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/v1/assets/from-project"),
        expect.objectContaining({ method: "POST" })
      );
    });
  });

  describe("applyAssetsToProject", () => {
    it("POSTs /api/v1/assets/apply-to-project", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        mockResponse({ jsonData: { succeeded: [], skipped: [], failed: [] } }),
      );
      vi.stubGlobal("fetch", fetchMock);
      await API.applyAssetsToProject({ asset_ids: ["1"], target_project: "demo", conflict_policy: "skip" });
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/v1/assets/apply-to-project"),
        expect.anything()
      );
    });
  });

  describe("getGlobalAssetUrl", () => {
    it("returns URL for valid path", () => {
      const url = API.getGlobalAssetUrl("_global_assets/character/abc.png", "123");
      expect(url).toContain("/global-assets/character/abc.png");
      expect(url).toContain("fp=123");
    });

    it("returns null for null path", () => {
      expect(API.getGlobalAssetUrl(null)).toBeNull();
    });

    it("returns null for non-global path", () => {
      expect(API.getGlobalAssetUrl("regular/path.png")).toBeNull();
    });
  });
});

import type { ReferenceVideoUnit } from "@/types";

describe("API.referenceVideos", () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let fetchMock: ReturnType<typeof vi.fn> & { mock: { calls: any[] } };

  beforeEach(() => {
    fetchMock = vi.spyOn(globalThis, "fetch");
  });

  afterAll(() => {
    vi.restoreAllMocks();
  });

  const mkUnit = (id: string): ReferenceVideoUnit => ({
    unit_id: id,
    shots: [{ duration: 3, text: "Shot 1 (3s): test" }],
    references: [],
    duration_seconds: 3,
    duration_override: false,
    transition_to_next: "cut",
    note: null,
    generated_assets: {
      storyboard_image: null,
      storyboard_last_image: null,
      grid_id: null,
      grid_cell_index: null,
      video_clip: null,
      video_uri: null,
      status: "pending",
    },
  });

  it("listReferenceVideoUnits calls GET /reference-videos/episodes/:ep/units", async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ units: [mkUnit("E1U1")] }), { status: 200 }));
    const res = await API.listReferenceVideoUnits("proj", 1);
    expect(res.units).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/projects/proj/reference-videos/episodes/1/units",
      expect.not.objectContaining({ method: expect.stringMatching(/./) }),
    );
  });

  it("addReferenceVideoUnit posts the prompt payload", async () => {
    const unit = mkUnit("E1U2");
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ unit }), { status: 201 }));
    const res = await API.addReferenceVideoUnit("proj", 1, { prompt: "Shot 1 (3s): hi", references: [] });
    expect(res.unit.unit_id).toBe("E1U2");
    const [, init] = fetchMock.mock.calls[0]!;
    expect(init!.method).toBe("POST");
    const body = JSON.parse(init!.body as string) as { prompt: string };
    expect(body.prompt).toBe("Shot 1 (3s): hi");
  });

  it("reorderReferenceVideoUnits sends ordered ids", async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ units: [] }), { status: 200 }));
    await API.reorderReferenceVideoUnits("proj", 1, ["E1U2", "E1U1"]);
    const body = JSON.parse(fetchMock.mock.calls[0]![1]!.body as string) as { unit_ids: string[] };
    expect(body.unit_ids).toEqual(["E1U2", "E1U1"]);
  });

  it("generateReferenceVideoUnit returns task id", async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ task_id: "t-1", deduped: false }), { status: 202 }));
    const res = await API.generateReferenceVideoUnit("proj", 1, "E1U1");
    expect(res.task_id).toBe("t-1");
  });

  it("deleteReferenceVideoUnit returns void on 204", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await expect(API.deleteReferenceVideoUnit("proj", 1, "E1U1")).resolves.toBeUndefined();
  });
});

describe("uploadFile (source) onConflict", () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });

  it("passes on_conflict query when provided", async () => {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response(JSON.stringify({ success: true, path: "source/a.txt", url: "/x" }), { status: 200 })
    );
    await API.uploadFile("p", "source", new File(["x"], "a.txt"), null, { onConflict: "replace" });
    const url = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]![0] as string;
    expect(url).toContain("on_conflict=replace");
  });

  it("throws ConflictError on 409 with structured detail", async () => {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          detail: { existing: "a.txt", suggested_name: "a_1", message: "conflict" },
        }),
        { status: 409 }
      )
    );
    await expect(
      API.uploadFile("p", "source", new File(["x"], "a.txt"))
    ).rejects.toBeInstanceOf(ConflictError);
  });

  it("ConflictError carries existing and suggestedName", async () => {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          detail: { existing: "a.txt", suggested_name: "a_1", message: "conflict" },
        }),
        { status: 409 }
      )
    );
    try {
      await API.uploadFile("p", "source", new File(["x"], "a.txt"));
      expect.unreachable();
    } catch (err) {
      expect(err).toBeInstanceOf(ConflictError);
      expect((err as ConflictError).existing).toBe("a.txt");
      expect((err as ConflictError).suggestedName).toBe("a_1");
    }
  });
});
