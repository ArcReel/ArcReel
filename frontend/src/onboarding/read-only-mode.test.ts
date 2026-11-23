import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API, ReadOnlyModeError, setApiReadOnly } from "@/api";
import { DEMO_PROJECT_NAME } from "@/onboarding/demo-project";


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
    await API.request("/projects/real-project/characters/hero", {
      method: "PATCH",
      body: "{}",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("blocks the demo project under any casing, matching isDemoProject", async () => {
    await expect(
      API.request(`/projects/${DEMO_PROJECT_NAME.toUpperCase()}/characters/hero`, {
        method: "PATCH",
        body: "{}",
      }),
    ).rejects.toThrow(ReadOnlyModeError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not treat a project literally named 'import' as project-less", async () => {
    await API.request("/projects/import/characters/hero", {
      method: "PATCH",
      body: "{}",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("still blocks the static archive-import endpoint itself", async () => {
    await expect(
      API.request("/projects/import", { method: "POST", body: "{}" }),
    ).rejects.toThrow(ReadOnlyModeError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("blocks task enqueueing", async () => {
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

  it("exempts the onboarding-seen system endpoint from the gate", async () => {
    await API.request("/onboarding/seen", { method: "POST" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("lets the tour mark itself seen from inside the demo workbench", async () => {
    await API.markOnboardingSeen();
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
    expect(API.getFileUrl("onboarding_demo", dataUri, null)).toBe(dataUri);
    expect(API.getFileUrl("onboarding_demo", dataUri, 3, { width: 320 })).toBe(dataUri);
  });

  it("still builds a project-relative URL for real asset paths", () => {
    expect(API.getFileUrl("demo", "storyboards/E1S1.png", null)).toContain(
      "storyboards/E1S1.png",
    );
  });
});
