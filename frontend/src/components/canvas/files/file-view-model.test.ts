import { describe, expect, it } from "vitest";

import type { FileViewEntry } from "@/types";

import { canDeleteFile, ownerTarget, previewKind } from "./file-view-model";

describe("ownerTarget", () => {
  it("routes assets to their gallery and focuses the card", () => {
    expect(ownerTarget({ kind: "asset", asset_type: "character", id: "阿青" }, "demo")).toMatchObject({
      href: "/characters",
      focus: { type: "character", id: "阿青" },
    });
  });

  it("routes storyboard media to the episode and focuses the item", () => {
    expect(ownerTarget({ kind: "segment", id: "E2S01", episode: 2 }, "demo")).toEqual({
      href: "/episodes/2",
      labelKey: "file_view_open_segment",
      focus: { type: "segment", id: "E2S01", route: "/episodes/2" },
    });
  });

  it("opens the edit timeline, the episodes view, memory and project settings", () => {
    expect(ownerTarget({ kind: "timeline", id: "tl-0123abcd", episode: 1 }, "demo")?.href).toBe(
      "/episodes/1?view=edit&tl=tl-0123abcd",
    );
    expect(ownerTarget({ kind: "source" }, "demo")?.href).toBe("/episodes");
    expect(ownerTarget({ kind: "memory", id: "MEMORY.md" }, "my demo")?.href).toBe(
      "~/app/projects/my%20demo/settings?tab=memory&file=MEMORY.md",
    );
    expect(ownerTarget({ kind: "project" }, "demo")?.href).toBe("~/app/projects/demo/settings");
  });

  it("has nowhere to go without an owner or an episode", () => {
    expect(ownerTarget(null, "demo")).toBeNull();
    expect(ownerTarget({ kind: "grid", id: "grid_0123456789ab" }, "demo")).toBeNull();
  });
});

describe("previewKind", () => {
  it("picks the preview by extension, case-insensitively", () => {
    expect(previewKind("scene_E1S01.PNG")).toBe("image");
    expect(previewKind("project.json")).toBe("text");
    expect(previewKind("draft.zip")).toBeNull();
    expect(previewKind("README")).toBeNull();
  });
});

describe("canDeleteFile", () => {
  const file = (role: FileViewEntry["role"], owner?: FileViewEntry["owner"]): FileViewEntry => ({
    name: "a.png",
    path: "a.png",
    type: "file",
    size: 1,
    modified_at: "2026-10-25T00:00:00+00:00",
    role,
    owner,
  });

  it("offers history versions and unreferenced files, not other roles or loose source files", () => {
    expect(canDeleteFile(file("history"))).toBe(true);
    expect(canDeleteFile(file("unreferenced"))).toBe(true);
    expect(canDeleteFile(file("unreferenced", { kind: "source" }))).toBe(false);
    expect(canDeleteFile(file("referenced"))).toBe(false);
    expect(canDeleteFile(file("memory"))).toBe(false);
  });

  it("hides delete for the current version snapshot", () => {
    expect(canDeleteFile({ ...file("history"), is_current: true })).toBe(false);
  });
});
