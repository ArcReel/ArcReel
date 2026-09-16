import { describe, expect, it } from "vitest";
import { flattenOutputSchema } from "./promptTemplateSchema";

describe("flattenOutputSchema", () => {
  it("flattens nested objects, arrays, enums and nullable fields into path rows", () => {
    const rows = flattenOutputSchema({
      type: "object",
      properties: {
        title: { type: "string", description: "剧集标题" },
        scenes: { type: "array", description: "分镜列表", items: { $ref: "#/$defs/Scene" } },
      },
      $defs: {
        Scene: {
          type: "object",
          properties: {
            shot_type: { $ref: "#/$defs/ShotType", description: "镜头类型" },
            speaker: { anyOf: [{ type: "string" }, { type: "null" }], description: "说话角色\n  名" },
            tags: { type: "array", items: { enum: ["a", "b"], type: "string" } },
          },
        },
        ShotType: { enum: ["Close-up", "Long Shot"], type: "string" },
      },
    });

    expect(rows).toEqual([
      { path: "title", type: "string", enumValues: [], nullable: false, description: "剧集标题" },
      { path: "scenes", type: "list[object]", enumValues: [], nullable: false, description: "分镜列表" },
      {
        path: "scenes[].shot_type",
        type: "enum",
        enumValues: ["Close-up", "Long Shot"],
        nullable: false,
        description: "镜头类型",
      },
      { path: "scenes[].speaker", type: "string", enumValues: [], nullable: true, description: "说话角色 名" },
      { path: "scenes[].tags", type: "list[enum]", enumValues: ["a", "b"], nullable: false, description: "" },
    ]);
  });

  it("stops descending into a self-referencing definition", () => {
    const rows = flattenOutputSchema({
      $defs: {
        Node: { type: "object", properties: { children: { type: "array", items: { $ref: "#/$defs/Node" } } } },
      },
      type: "object",
      properties: { root: { $ref: "#/$defs/Node" } },
    });

    expect(rows.map((row) => row.path)).toEqual(["root", "root.children"]);
  });
});
