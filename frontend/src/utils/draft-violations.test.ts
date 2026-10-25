import { describe, expect, it } from "vitest";
import i18n from "@/i18n";
import { draftViolationMessage } from "./draft-violations";

describe("draftViolationMessage", () => {
  it("uses the draft position even when the internal number is far ahead", () => {
    expect(draftViolationMessage({
      label: "segment E1S85",
      message: "segment E1S85 的原文为空",
      item_index: 1,
    }, i18n.t)).toBe("分镜 2 的原文为空");
  });

  it("uses the formal position for a prompt draft covering a subset", () => {
    expect(draftViolationMessage({
      label: "unit E1U85",
      message: "unit E1U85 使用了全角花括号",
      item_index: 0,
    }, i18n.t, 7)).toBe("视频单元 7 使用了全角花括号");
  });

  it("replaces the item ID in a soft warning without a label", () => {
    expect(draftViolationMessage({
      code: "long_text",
      params: {},
      item_id: "E1S85",
      item_index: 1,
      message: "E1S85 的原文偏长",
    }, i18n.t)).toBe("分镜 2 的原文偏长");
  });

  it("shows deleted when a prompt draft's formal item has been removed", () => {
    expect(draftViolationMessage({
      label: "unit E1U85",
      message: "unit E1U85 使用了全角花括号",
      item_index: 0,
    }, i18n.t, null)).toBe("已删除 使用了全角花括号");
  });
});
