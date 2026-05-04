import { describe, it, expect } from "vitest";
import {
  parseDurationInput,
  isContinuousIntegerRange,
  compactRangeFormat,
  formatDurationsLabel,
  DurationParseError,
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

  // 用工具函数：先 .toThrow 强断言确实抛错，再捕获验证 code/params
  const expectErr = (input: string, code: string) => {
    expect(() => parseDurationInput(input)).toThrow(DurationParseError);
    let caught: DurationParseError | null = null;
    try {
      parseDurationInput(input);
    } catch (e) {
      caught = e as DurationParseError;
    }
    expect(caught).toBeInstanceOf(DurationParseError);
    expect(caught!.code).toBe(code);
  };

  it("仅含分隔符的输入抛 empty_after_split", () => {
    // 防止 "," / ", ," 等被静默解析为 []
    for (const c of [",", ", ,", " , , ", ",,,"]) {
      expectErr(c, "empty_after_split");
    }
  });

  it("非法片段抛带 code 的 DurationParseError", () => {
    expectErr("abc", "unparseable");
    expectErr("4, abc", "unparseable");
    expectErr("10-3", "range_inverted");
    expectErr("0-5", "non_positive");
    expectErr("-3", "unparseable");
    expectErr("4--6", "unparseable");
  });

  it("拒绝过大区间", () => {
    expectErr("1-100", "range_too_large");
  });

  it("拒绝超出单值上限 60 秒", () => {
    expectErr("99999", "exceeds_max");
    expectErr("4, 100", "exceeds_max");
    // 99 - 4 = 95 > MAX_RANGE_SPAN(30)，range_too_large 优先
    expect(() => parseDurationInput("4-99")).toThrow(DurationParseError);
    let caught: DurationParseError | null = null;
    try {
      parseDurationInput("4-99");
    } catch (e) {
      caught = e as DurationParseError;
    }
    expect(["range_too_large", "exceeds_max"]).toContain(caught!.code);
  });

  it("60 作为单值仍合法", () => {
    expect(parseDurationInput("60")).toEqual([60]);
  });

  it("1-30 作为区间仍合法", () => {
    expect(parseDurationInput("1-30")).toHaveLength(30);
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
