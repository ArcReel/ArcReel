import { describe, expect, it } from "vitest";
import { formatNameList } from "./list-format";

describe("formatNameList", () => {
  it("中文用顿号分隔", () => {
    expect(formatNameList(["甲", "乙", "丙"], "zh")).toBe("甲、乙、丙");
  });

  it("英文用逗号分隔", () => {
    expect(formatNameList(["a", "b", "c"], "en")).toBe("a, b, c");
  });

  it("单元素原样返回", () => {
    expect(formatNameList(["only"], "vi")).toBe("only");
  });

  it("非法语言标签回退默认 locale 而不抛错", () => {
    expect(formatNameList(["a", "b"], "not a locale")).toContain("a");
  });
});
