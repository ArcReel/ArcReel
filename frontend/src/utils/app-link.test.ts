import { describe, expect, it } from "vitest";

import { parseAppLink, parseSeconds } from "./app-link";

const ORIGIN = "https://studio.example.com";

describe("parseAppLink", () => {
  it("剪辑视图链接原样保留 view、tl、t，由剪辑视图自己消费", () => {
    const link = parseAppLink("/app/projects/demo/episodes/1?view=edit&tl=tl-00000002&t=12.5", ORIGIN);
    expect(link).toEqual({
      href: "/app/projects/demo/episodes/1?view=edit&tl=tl-00000002&t=12.5",
      to: "/app/projects/demo/episodes/1?view=edit&tl=tl-00000002&t=12.5",
      unit: null,
    });
  });

  it("单元链接拆出单元与起始时间，地址里去掉这两个一次性参数", () => {
    const link = parseAppLink("/app/projects/demo/episodes/2?unit=E1U3&t=4.5", ORIGIN);
    expect(link).toEqual({
      href: "/app/projects/demo/episodes/2?unit=E1U3&t=4.5",
      to: "/app/projects/demo/episodes/2",
      unit: { id: "E1U3", seconds: 4.5 },
    });
  });

  it("单元链接不带 t 时起始时间为 null", () => {
    expect(parseAppLink("/app/projects/demo/episodes/2?unit=E1U3", ORIGIN)?.unit).toEqual({ id: "E1U3", seconds: null });
  });

  it("项目名按路径段编码", () => {
    const link = parseAppLink("/app/projects/%E6%88%91%E7%9A%84%E9%A1%B9%E7%9B%AE/episodes/1?unit=E1U1", ORIGIN);
    expect(link?.to).toBe("/app/projects/%E6%88%91%E7%9A%84%E9%A1%B9%E7%9B%AE/episodes/1");
  });

  it("同源的绝对地址同样算应用内链接", () => {
    expect(parseAppLink(`${ORIGIN}/app/projects/demo/episodes/1?view=edit`, ORIGIN)?.to).toBe(
      "/app/projects/demo/episodes/1?view=edit",
    );
  });

  it("不以斜杠开头的写法按站点根解析，href 规范成以斜杠开头的完整地址", () => {
    expect(parseAppLink("app/projects/demo/episodes/1?view=edit&t=4", ORIGIN)?.href).toBe(
      "/app/projects/demo/episodes/1?view=edit&t=4",
    );
  });

  it("剪辑视图链接里的 unit 不当作单元链接", () => {
    expect(parseAppLink("/app/projects/demo/episodes/1?view=edit&unit=E1U1&t=3", ORIGIN)?.unit).toBeNull();
  });

  it("非集页路径上的 unit 不当作单元链接", () => {
    expect(parseAppLink("/app/projects/demo/characters?unit=E1U1", ORIGIN)?.unit).toBeNull();
  });

  it.each([
    "https://example.com/app/projects/demo",
    "//example.com/app/projects/demo",
    "/\\example.com/app/projects/demo",
    "/api/v1/projects/demo/export",
    "/application",
    "/app/../api/v1/projects",
    "javascript:alert(1)",
    "mailto:a@b.c",
  ])("%s 不是应用内链接", (href) => {
    expect(parseAppLink(href, ORIGIN)).toBeNull();
  });
});

describe("parseSeconds", () => {
  it.each([
    ["0", 0],
    ["12", 12],
    ["12.5", 12.5],
  ])("%s -> %s", (raw, expected) => {
    expect(parseSeconds(raw)).toBe(expected);
  });

  it.each([null, "", "abc", "-1", "1e3", "1.", ".5", "Infinity", "1,5"])("%s 视为没有写", (raw) => {
    expect(parseSeconds(raw)).toBeNull();
  });
});
