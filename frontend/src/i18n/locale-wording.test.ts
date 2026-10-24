import { describe, expect, it } from "vitest";

type Messages = Record<string, unknown>;

const zh = import.meta.glob<Messages>("./zh/*.ts", { eager: true, import: "default" });
const vi = import.meta.glob<Messages>("./vi/*.ts", { eager: true, import: "default" });
const en = import.meta.glob<Messages>("./en/*.ts", { eager: true, import: "default" });

function entries(modules: Record<string, Messages>): [string, string][] {
  const out: [string, string][] = [];
  const walk = (prefix: string, value: unknown) => {
    if (typeof value === "string") out.push([prefix, value]);
    else if (value && typeof value === "object") {
      for (const [k, v] of Object.entries(value)) walk(`${prefix}.${k}`, v);
    }
  };
  for (const [path, messages] of Object.entries(modules)) walk(path, messages);
  return out;
}

// 后端 zh 包按同一口径检查：tests/unit/lib/i18n/test_locale_wording.py
describe("locale wording", () => {
  it("uses a full-width colon after Chinese text in the zh pack", () => {
    const offenders = entries(zh).filter(([, text]) => /\p{Script=Han}\s?:/u.test(text));
    expect(offenders).toEqual([]);
  });

  it("uses「…」for ellipses and「」for quotes in the zh pack", () => {
    // 只查成对的引号：列举非法字符时单独出现的 " 是字符本身
    const offenders = entries(zh).filter(([, text]) => /\.\.\.|"[^"]*"/.test(text));
    expect(offenders).toEqual([]);
  });

  it("writes value ranges with「～」in the zh pack", () => {
    // 端点含占位符或百分号、或后接单位的才算范围；两端都是裸数字的（如时长档位简写 3-15）是用户照样输入的语法
    const range =
      /\{\{\w+\}\}\s?[-–]\s?(\d|\{\{)|\d\s?[-–]\s?\{\{|\d%\s?[-–]\s?\d|\d\s?[-–]\s?\d+(%|\s?[\p{Script=Han}A-Za-z])/u;
    const offenders = entries(zh).filter(([, text]) => range.test(text));
    expect(offenders).toEqual([]);
  });

  it.each([
    ["en", en],
    ["vi", vi],
  ])("keeps Chinese text and full-width punctuation out of the %s pack", (_, modules) => {
    const offenders = entries(modules).filter(([, text]) => /[\p{Script=Han}\u3000-\u303f\uff01-\uff5e]/u.test(text));
    expect(offenders).toEqual([]);
  });

  it("calls a derivative「phái sinh」throughout the vi pack, never「biến thể」", () => {
    const offenders = entries(vi).filter(([, text]) => /biến thể/iu.test(text));
    expect(offenders).toEqual([]);
  });
});
