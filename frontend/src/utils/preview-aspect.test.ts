import { describe, expect, it } from "vitest";

import { previewAspect } from "./preview-aspect";

describe("previewAspect", () => {
  it("uses the project's configured aspect ratio, including the storyboard entry of legacy settings", () => {
    expect(previewAspect({ content_mode: "narration", aspect_ratio: "16:9" })).toBe("16:9");
    expect(previewAspect({ content_mode: "drama", aspect_ratio: { storyboard: "9:16" } })).toBe("9:16");
  });

  it("falls back to portrait for narration and ads and to landscape otherwise", () => {
    expect(previewAspect({ content_mode: "narration" })).toBe("9:16");
    expect(previewAspect({ content_mode: "ad" })).toBe("9:16");
    expect(previewAspect({ content_mode: "drama" })).toBe("16:9");
  });

  it("previews other ratios in landscape", () => {
    expect(previewAspect({ content_mode: "narration", aspect_ratio: "1:1" })).toBe("16:9");
  });
});
