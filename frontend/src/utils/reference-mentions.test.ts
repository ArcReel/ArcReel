import { describe, it, expect } from "vitest";
import {
  extractMentions,
  resolveMentionType,
  mergeReferences,
} from "./reference-mentions";
import type { ProjectData } from "@/types";
import type { ReferenceResource } from "@/types/reference-video";

function mkProject(): Pick<ProjectData, "characters" | "scenes" | "props"> {
  return {
    characters: { 主角: { description: "" }, 张三: { description: "" } },
    scenes: { 酒馆: { description: "" } },
    props: { 长剑: { description: "" } },
  };
}

describe("extractMentions", () => {
  it("returns unique mention names in first-occurrence order", () => {
    expect(extractMentions("@a @b @a @c")).toEqual(["a", "b", "c"]);
  });

  it("returns empty list when no mentions", () => {
    expect(extractMentions("Shot 1 (3s): plain text")).toEqual([]);
  });

  it("matches CJK characters and underscores", () => {
    expect(extractMentions("@主角 and @张_三")).toEqual(["主角", "张_三"]);
  });
});

describe("resolveMentionType", () => {
  const project = mkProject();

  it("prefers character → scene → prop", () => {
    expect(resolveMentionType(project, "主角")).toBe("character");
    expect(resolveMentionType(project, "酒馆")).toBe("scene");
    expect(resolveMentionType(project, "长剑")).toBe("prop");
  });

  it("returns undefined for unknown names", () => {
    expect(resolveMentionType(project, "路人")).toBeUndefined();
  });
});

describe("mergeReferences", () => {
  const project = mkProject();

  it("appends new mentions at the end, preserving existing order", () => {
    const existing: ReferenceResource[] = [
      { type: "character", name: "张三" },
    ];
    const merged = mergeReferences("Shot 1 (3s): @张三 @主角", existing, project);
    expect(merged).toEqual([
      { type: "character", name: "张三" },
      { type: "character", name: "主角" },
    ]);
  });

  it("removes references whose names are no longer in prompt", () => {
    const existing: ReferenceResource[] = [
      { type: "character", name: "张三" },
      { type: "scene", name: "酒馆" },
    ];
    const merged = mergeReferences("Shot 1 (3s): @张三", existing, project);
    expect(merged).toEqual([{ type: "character", name: "张三" }]);
  });

  it("skips unknown mentions (not resolvable to any bucket)", () => {
    const merged = mergeReferences("Shot 1 (3s): @路人 @主角", [], project);
    expect(merged).toEqual([{ type: "character", name: "主角" }]);
  });

  it("deduplicates repeated mentions", () => {
    const merged = mergeReferences("Shot 1 (3s): @主角 @主角 @主角", [], project);
    expect(merged).toEqual([{ type: "character", name: "主角" }]);
  });

  it("returns empty list when prompt has no valid mentions", () => {
    expect(mergeReferences("Shot 1 (3s): plain", [], project)).toEqual([]);
  });
});
