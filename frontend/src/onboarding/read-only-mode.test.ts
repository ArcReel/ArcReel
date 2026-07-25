import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API, ReadOnlyModeError, setApiReadOnly } from "@/api";
import { DEMO_PROJECT_NAME } from "@/onboarding/demo-project";

/**
 * 只读闸门的结构性保证：`withAuth()` 是全部 fetch 的唯一出口，闸门落在那里，
 * 所以「哪个按钮忘了禁用」不会变成一次真实写请求。
 */

function okResponse(): Response {
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    headers: new Headers(),
    json: vi.fn().mockResolvedValue({}),
    text: vi.fn().mockResolvedValue(""),
    blob: vi.fn().mockResolvedValue(new Blob()),
  } as unknown as Response;
}

describe("read-only demo mode", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue(okResponse());
    vi.stubGlobal("fetch", fetchMock);
    setApiReadOnly(true);
  });

  afterEach(() => {
    setApiReadOnly(false);
    vi.unstubAllGlobals();
  });

  it("blocks writes before they reach the network", async () => {
    await expect(
      API.request("/projects", { method: "POST", body: "{}" }),
    ).rejects.toThrow(ReadOnlyModeError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("blocks every non-GET method, not just POST", async () => {
    for (const method of ["POST", "PUT", "PATCH", "DELETE", "post"]) {
      await expect(
        API.request(`/projects/${DEMO_PROJECT_NAME}/x`, { method }),
      ).rejects.toThrow(ReadOnlyModeError);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("blocks writes with no project in the URL (global endpoints)", async () => {
    await expect(
      API.request("/assets", { method: "POST", body: "{}" }),
    ).rejects.toThrow(ReadOnlyModeError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not block writes aimed at a different, real project", async () => {
    // 真实项目发起的多请求写操作，若在两次请求之间导航进了演示工作台，
    // 闸门此时是全局态而非按请求目标判定——但只要请求本身写的不是演示项目，
    // 就不该被这个兜底拦下，否则会留下部分完成的真实项目写入。
    await API.request("/projects/real-project/characters/hero", {
      method: "PATCH",
      body: "{}",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("blocks task enqueueing", async () => {
    // 入队端点一律是 POST，所以闸门对它们的覆盖和对普通写操作一样
    await expect(
      API.request(`/projects/${DEMO_PROJECT_NAME}/generate/storyboard/E1S1`, {
        method: "POST",
        body: JSON.stringify({ prompt: "p", script_file: "E1.json" }),
      }),
    ).rejects.toThrow(ReadOnlyModeError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("still allows reads", async () => {
    await API.request("/projects");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("lifts the gate when the demo workbench is left", async () => {
    setApiReadOnly(false);
    await API.request("/projects", { method: "POST", body: "{}" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("getFileUrl", () => {
  it("passes inline placeholder URIs through untouched", () => {
    const dataUri = "data:image/svg+xml;charset=utf-8,%3Csvg%2F%3E";
    expect(API.getFileUrl("onboarding_demo", dataUri)).toBe(dataUri);
  });

  it("still builds a project-relative URL for real asset paths", () => {
    expect(API.getFileUrl("demo", "storyboards/E1S1.png")).toContain(
      "storyboards/E1S1.png",
    );
  });
});
