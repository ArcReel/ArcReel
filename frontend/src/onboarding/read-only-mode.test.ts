import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API, ReadOnlyModeError, setApiReadOnly } from "@/api";

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
      await expect(API.request("/projects/x", { method })).rejects.toThrow(
        ReadOnlyModeError,
      );
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("blocks task enqueueing", async () => {
    // 入队端点一律是 POST，所以闸门对它们的覆盖和对普通写操作一样
    await expect(
      API.request("/projects/onboarding_demo/generate/storyboard/E1S1", {
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
