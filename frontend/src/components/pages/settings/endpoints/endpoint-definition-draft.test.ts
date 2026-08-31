import { describe, expect, it } from "vitest";
import {
  definitionFileName,
  isRenderableDefinition,
  newEndpointDefinition,
  readPaths,
  renameKey,
  sectionOfIssuePath,
  writePaths,
} from "./endpoint-definition-draft";

describe("readPaths / writePaths", () => {
  it("reads both the shorthand array and the full object form", () => {
    expect(readPaths(["$.a", "$.b"])).toEqual(["$.a", "$.b"]);
    expect(readPaths({ paths: ["$.a"], accept: "scalar" })).toEqual(["$.a"]);
    expect(readPaths(undefined)).toEqual([]);
  });

  it("keeps accept when writing back to the full object form", () => {
    expect(writePaths({ paths: ["$.a"], accept: "scalar" }, ["$.b"])).toEqual({
      paths: ["$.b"],
      accept: "scalar",
    });
  });

  it("stays in shorthand form when the original had no accept", () => {
    expect(writePaths(["$.a"], ["$.b"])).toEqual(["$.b"]);
    expect(writePaths(undefined, ["$.b"])).toEqual(["$.b"]);
  });

  it("writes an empty list rather than dropping the field", () => {
    expect(writePaths(["$.a"], [])).toEqual([]);
  });

  it("carries json_decode path items through a round trip untouched", () => {
    const spec = [{ path: "$.payload", json_decode: true, then: ["$.url"] }];
    expect(writePaths(spec, readPaths(spec))).toEqual(spec);
  });
});

describe("sectionOfIssuePath", () => {
  it("maps a diagnostic path onto the form section that owns it", () => {
    expect(sectionOfIssuePath("poll.extract.video_url[0]")).toBe("poll");
    expect(sectionOfIssuePath("result.extract.video_url")).toBe("poll");
    expect(sectionOfIssuePath("submit.body.model")).toBe("submit");
    expect(sectionOfIssuePath("auth.headers.Authorization")).toBe("auth");
    expect(sectionOfIssuePath("status_map.pending")).toBe("status");
    expect(sectionOfIssuePath("capabilities.first_frame")).toBe("capabilities");
    expect(sectionOfIssuePath("inputs.first_frame.source")).toBe("inputs");
  });

  it("claims no section for fields the form has no controls for", () => {
    expect(sectionOfIssuePath("$")).toBeNull();
    expect(sectionOfIssuePath("enum_maps.resolution")).toBeNull();
    expect(sectionOfIssuePath("defaults.resolution")).toBeNull();
    expect(sectionOfIssuePath("schema_version")).toBeNull();
  });
});

describe("renameKey", () => {
  it("renames in place instead of moving the entry to the end", () => {
    const renamed = renameKey({ a: 1, b: 2, c: 3 }, "b", "z", 2);
    expect(Object.keys(renamed)).toEqual(["a", "z", "c"]);
    expect(renamed.z).toBe(2);
  });

  it("keeps both rows when the target key already exists", () => {
    const original = { Authorization: "Bearer {{ api_key }}", "X-Token": "abc" };

    expect(renameKey(original, "Authorization", "X-Token", "Bearer {{ api_key }}")).toEqual(
      original,
    );
  });
});

describe("newEndpointDefinition", () => {
  it("prefills the method and an identity status mapping", () => {
    const definition = newEndpointDefinition("Ada");
    expect(definition.meta.author).toBe("Ada");
    expect(definition.submit.method).toBe("POST");
    expect(definition.status_map).toEqual({
      queued: "queued",
      processing: "running",
      succeeded: "succeeded",
      failed: "failed",
    });
  });

  it("leaves capabilities and extraction paths for the author to fill in", () => {
    const definition = newEndpointDefinition("Ada");
    expect(definition.capabilities).toBeUndefined();
    expect(readPaths(definition.submit.extract.task_id)).toEqual([]);
    expect(readPaths(definition.poll.extract.status)).toEqual([]);
  });
});

describe("isRenderableDefinition", () => {
  it("accepts a freshly prefilled definition", () => {
    expect(isRenderableDefinition(newEndpointDefinition("Ada"))).toBe(true);
  });

  it("rejects syntactically valid JSON that misses a dereferenced container", () => {
    expect(isRenderableDefinition({})).toBe(false);
    expect(isRenderableDefinition(null)).toBe(false);
    expect(isRenderableDefinition([])).toBe(false);
    const noSubmitExtract = newEndpointDefinition("Ada") as unknown as Record<string, unknown>;
    noSubmitExtract.submit = { method: "POST", url: "" };
    expect(isRenderableDefinition(noSubmitExtract)).toBe(false);
    const noPollExtract = newEndpointDefinition("Ada") as unknown as Record<string, unknown>;
    noPollExtract.poll = { method: "GET", url: "" };
    expect(isRenderableDefinition(noPollExtract)).toBe(false);
    const noMeta = newEndpointDefinition("Ada") as unknown as Record<string, unknown>;
    delete noMeta.meta;
    expect(isRenderableDefinition(noMeta)).toBe(false);
  });

  it("tolerates missing leaf fields inside the containers", () => {
    const sparse = {
      meta: {},
      submit: { extract: {} },
      poll: { extract: {} },
    };
    expect(isRenderableDefinition(sparse)).toBe(true);
  });
});

describe("definitionFileName", () => {
  it("derives an ASCII-safe file name from the endpoint name", () => {
    const definition = newEndpointDefinition("Ada");
    definition.meta.name = "Example Video API";
    expect(definitionFileName(definition)).toBe("Example-Video-API.json");
  });

  it("falls back to a generic name when the endpoint is unnamed", () => {
    expect(definitionFileName(newEndpointDefinition("Ada"))).toBe("endpoint.json");
  });
});
