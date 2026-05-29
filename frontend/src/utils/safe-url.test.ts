import { describe, expect, it } from "vitest";
import { safeReturnPath } from "@/utils/safe-url";

describe("safeReturnPath", () => {
  it("allows internal /app/ paths and preserves the query string", () => {
    expect(safeReturnPath("/app/projects/demo")).toBe("/app/projects/demo");
    expect(safeReturnPath("/app/projects/demo?tab=scene")).toBe("/app/projects/demo?tab=scene");
  });

  it("preserves the URL hash alongside path and query", () => {
    expect(safeReturnPath("/app/projects/demo#shot-3")).toBe("/app/projects/demo#shot-3");
    expect(safeReturnPath("/app/projects/demo?tab=scene#shot-3")).toBe(
      "/app/projects/demo?tab=scene#shot-3",
    );
  });

  it("rejects internal paths outside /app/", () => {
    expect(safeReturnPath("/login")).toBeNull();
    expect(safeReturnPath("/")).toBeNull();
    expect(safeReturnPath("/app")).toBeNull();
  });

  it("rejects external and protocol-relative URLs (open redirect)", () => {
    expect(safeReturnPath("//evil.com")).toBeNull();
    expect(safeReturnPath("http://evil.com")).toBeNull();
    expect(safeReturnPath("https://evil.com/app/x")).toBeNull();
  });

  it("rejects traversal that escapes /app/", () => {
    expect(safeReturnPath("/app/../../evil")).toBeNull();
  });

  it("returns null for empty or missing input", () => {
    expect(safeReturnPath(null)).toBeNull();
    expect(safeReturnPath(undefined)).toBeNull();
    expect(safeReturnPath("")).toBeNull();
  });
});
