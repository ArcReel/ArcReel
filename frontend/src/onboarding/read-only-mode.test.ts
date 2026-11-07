import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API, ReadOnlyModeError, setApiReadOnly } from "@/api";
import { DEMO_PROJECT_NAME } from "@/onboarding/demo-project";

/**
 * Structural readonly guarantees: `withAuth()` is the sole fetch exit, carrying gates,
 * so missed disabled buttons never become real write requests.
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
    // Real-project multirequest writes may navigate into demos between requests;
    // gates are then global rather than target-scoped, but requests not writing demos
    // must bypass fallback blocking to avoid partially completed real-project writes.
    await API.request("/projects/real-project/characters/hero", {
      method: "PATCH",
      body: "{}",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("blocks the demo project under any casing, matching isDemoProject", async () => {
    // Gates must match case-insensitive isDemoProject; exact comparisons would classify
    // case-variant demo names as other real projects and permit writes.
    await expect(
      API.request(`/projects/${DEMO_PROJECT_NAME.toUpperCase()}/characters/hero`, {
        method: "PATCH",
        body: "{}",
      }),
    ).rejects.toThrow(ReadOnlyModeError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not treat a project literally named 'import' as project-less", async () => {
    // ProjectManager.normalize_project_name permits "import", also directly accepted by archive imports;
    // exclude only exact static /projects/import endpoints themselves,
    // never misclassify writes to projects named import as unowned and block them.
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
    // All enqueue endpoints are POST, giving gates identical coverage to ordinary writes.
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
    // Exiting tours writes seen markers while still on demo routes before apiReadOnly resets;
    // they write no project data and must bypass default unowned-request blocking.
    await API.request("/onboarding/seen", { method: "POST" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("lets the tour mark itself seen from inside the demo workbench", async () => {
    // Four workbench steps run read-only; Skip/Finish writes precisely these markers.
    // Exercise actual API methods rather than handwritten endpoints so method-path changes also fail tests.
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
