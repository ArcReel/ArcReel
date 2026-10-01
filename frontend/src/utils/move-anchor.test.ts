import { describe, expect, it } from "vitest";
import { dropAnchor, stepAnchor } from "./move-anchor";

const IDS = ["A", "B", "C", "D"];

describe("dropAnchor", () => {
  it("lands after the target when dragging down", () => {
    expect(dropAnchor(IDS, 0, 2)).toBe("C");
  });

  it("lands before the target when dragging up, or at the front onto the first item", () => {
    expect(dropAnchor(IDS, 3, 1)).toBe("A");
    expect(dropAnchor(IDS, 2, 0)).toBeNull();
  });

  it("does not move when dropped on itself or with an unknown index", () => {
    expect(dropAnchor(IDS, 1, 1)).toBeUndefined();
    expect(dropAnchor(IDS, -1, 2)).toBeUndefined();
  });
});

describe("stepAnchor", () => {
  it("moves earlier after the item two places up, or to the front from the second place", () => {
    expect(stepAnchor(IDS, 2, "earlier")).toBe("A");
    expect(stepAnchor(IDS, 1, "earlier")).toBeNull();
  });

  it("moves later after the next item", () => {
    expect(stepAnchor(IDS, 1, "later")).toBe("C");
  });

  it("does not move past either end", () => {
    expect(stepAnchor(IDS, 0, "earlier")).toBeUndefined();
    expect(stepAnchor(IDS, 3, "later")).toBeUndefined();
    expect(stepAnchor(IDS, -1, "later")).toBeUndefined();
  });
});
