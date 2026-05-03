import { describe, it, expect } from "vitest";
import {
  parseDurationInput,
  isContinuousIntegerRange,
  compactRangeFormat,
  formatDurationsLabel,
} from "./duration_format";

describe("parseDurationInput", () => {
  it("解析单值列表", () => {
    expect(parseDurationInput("4, 6, 8")).toEqual([4, 6, 8]);
  });

  it("解析区间简写", () => {
    expect(parseDurationInput("3-15")).toEqual([3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]);
  });

  it("混合单值与区间，去重排序", () => {
    expect(parseDurationInput("3, 5, 7-10, 12")).toEqual([3, 5, 7, 8, 9, 10, 12]);
  });

  it("空白容忍", () => {
    expect(parseDurationInput("  4 , 6 ")).toEqual([4, 6]);
  });

  it("空字符串返回 null", () => {
    expect(parseDurationInput("")).toBeNull();
    expect(parseDurationInput("   ")).toBeNull();
  });

  it("非法片段抛错", () => {
    expect(() => parseDurationInput("abc")).toThrow();
    expect(() => parseDurationInput("4, abc")).toThrow();
    expect(() => parseDurationInput("10-3")).toThrow();
    expect(() => parseDurationInput("0-5")).toThrow(); // 0 非正
    expect(() => parseDurationInput("-3")).toThrow();
    expect(() => parseDurationInput("4--6")).toThrow();
  });

  it("拒绝过大区间", () => {
    expect(() => parseDurationInput("1-100")).toThrow(/区间过大/);
  });
});

describe("isContinuousIntegerRange", () => {
  it("正例", () => {
    expect(isContinuousIntegerRange([3, 4, 5, 6, 7])).toBe(true);
    expect(isContinuousIntegerRange([1, 2, 3])).toBe(true);
  });

  it("负例：跳值", () => {
    expect(isContinuousIntegerRange([4, 6, 8])).toBe(false);
    expect(isContinuousIntegerRange([1, 3, 5])).toBe(false);
  });

  it("边界：单值与空", () => {
    expect(isContinuousIntegerRange([5])).toBe(false);
    expect(isContinuousIntegerRange([])).toBe(false);
  });

  it("无序输入也能识别（内部排序）", () => {
    expect(isContinuousIntegerRange([7, 5, 6, 8, 4])).toBe(true);
  });
});

describe("compactRangeFormat", () => {
  it("纯连续 → 折叠", () => {
    expect(compactRangeFormat([3, 4, 5, 6, 7])).toBe("3-7");
  });

  it("混合", () => {
    expect(compactRangeFormat([3, 4, 5, 7, 8, 9, 10, 12])).toBe("3-5, 7-10, 12");
  });

  it("纯离散", () => {
    expect(compactRangeFormat([4, 6, 8])).toBe("4, 6, 8");
  });

  it("单值", () => {
    expect(compactRangeFormat([6])).toBe("6");
  });

  it("空", () => {
    expect(compactRangeFormat([])).toBe("");
  });

  it("往返一致：parse → compact", () => {
    expect(compactRangeFormat(parseDurationInput("3-5, 7-10, 12")!)).toBe("3-5, 7-10, 12");
  });
});

describe("formatDurationsLabel", () => {
  it("简短 trailing s", () => {
    expect(formatDurationsLabel([4, 6, 8])).toBe("4, 6, 8s");
  });
  it("区间 trailing s", () => {
    expect(formatDurationsLabel([3, 4, 5, 6, 7])).toBe("3-7s");
  });
});
