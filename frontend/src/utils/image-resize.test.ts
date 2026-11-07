import { describe, expect, it } from "vitest";
import { resizedSize } from "./image-resize";

describe("resizedSize", () => {
  it.each([
    // Official A4-scan example: both edges within 1568 px, but vision tokens exceed budgets.
    { label: "A4 scan", input: [1075, 1520], expected: [924, 1307] },
    // Edge limits appear to allow 1568×882, actually narrowed by token budgets.
    { label: "1080p screenshot", input: [1920, 1080], expected: [1456, 819] },
    // Narrow phone screenshots finally use edge limits.
    { label: "phone screenshot", input: [1179, 2556], expected: [723, 1568] },
  ])("matches the documented sample for a $label", ({ input, expected }) => {
    expect(resizedSize(input[0], input[1])).toEqual(expected);
  });

  it.each([
    [800, 600],
    [1568, 28],
    [1, 1],
  ])("returns %ix%i unchanged when it already fits both limits", (width, height) => {
    expect(resizedSize(width, height)).toEqual([width, height]);
  });

  it("keeps every resized size within the edge and token limits", () => {
    const [width, height] = resizedSize(4000, 3000);

    expect(Math.max(width, height)).toBeLessThanOrEqual(1568);
    expect(Math.ceil(width / 28) * Math.ceil(height / 28)).toBeLessThanOrEqual(1568);
  });

  it("honours the high-resolution tier limits when they are passed", () => {
    expect(resizedSize(1075, 1520, 2576, 4784)).toEqual([1075, 1520]);
  });
});
