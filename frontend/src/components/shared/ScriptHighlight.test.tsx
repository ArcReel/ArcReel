import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ScriptHighlight } from "./ScriptHighlight";
import type { MentionLookup } from "@/hooks/useShotPromptHighlight";

const LOOKUP: MentionLookup = { 张三: "character", 酒馆: "scene", 长剑: "prop" };

function renderScript(text: string) {
  const { container } = render(<ScriptHighlight text={text} lookup={LOOKUP} />);
  return container;
}

describe("ScriptHighlight", () => {
  it("colors mentions by asset kind and flags unregistered ones", () => {
    const container = renderScript("镜头1：@张三 在 @酒馆 举起 @长剑，@王五 旁观。");
    const classFor = (name: string) =>
      [...container.querySelectorAll("span")].find((el) => el.textContent === `@${name}`)?.className ?? "";
    expect(classFor("张三")).toContain("sky");
    expect(classFor("酒馆")).toContain("emerald");
    expect(classFor("长剑")).toContain("amber");
    expect(classFor("王五")).toContain("red");
  });

  it("renders a normative dialogue line as speaker + spoken text, not raw syntax", () => {
    renderScript("镜头1：中景。\n@[张三]：{我来了}");
    expect(screen.getByText("张三")).toBeTruthy();
    expect(screen.getByText("我来了")).toBeTruthy();
    // 花括号与冒号是书写语法，解析视图里不再出现
    expect(screen.queryByText(/\{我来了\}/)).toBeNull();
  });

  it("labels a bare braces line as voiceover", () => {
    renderScript("镜头1：中景。\n{那年冬天格外冷}");
    expect(screen.getByText("画外音")).toBeTruthy();
    expect(screen.getByText("那年冬天格外冷")).toBeTruthy();
  });

  it("keeps shot headers flush left and indents the lines under them", () => {
    const container = renderScript("镜头1：中景。\n@[张三]：{我来了}\n镜头2：近景。");
    const rows = [...container.querySelectorAll<HTMLElement>(":scope > div > div")];
    const header = rows.find((r) => r.textContent?.startsWith("镜头1"));
    const dialogue = rows.find((r) => r.textContent?.includes("我来了"));
    expect(header?.className).not.toContain("ml-4");
    expect(dialogue?.className).toContain("ml-4");
  });

  it("parses a dialogue written on the shot header line, keeping the header its own row", () => {
    // 后端切分镜头时剥掉 header，这行在 shot 文本里就是规范台词行；预览若按描述行渲染，
    // 会与同屏的服务端派生台词列表自相矛盾。
    const container = renderScript("镜头1：@[张三]：{我来了}");
    expect(screen.getByText("张三")).toBeTruthy();
    expect(screen.getByText("我来了")).toBeTruthy();
    expect(screen.queryByText(/\{我来了\}/)).toBeNull();
    const rows = [...container.querySelectorAll<HTMLElement>(":scope > div > div")];
    expect(rows.find((r) => r.textContent?.trim() === "镜头1：")).toBeTruthy();
  });

  it("parses a voiceover written on the shot header line", () => {
    renderScript("镜头1：{那年冬天格外冷}");
    expect(screen.getByText("画外音")).toBeTruthy();
    expect(screen.getByText("那年冬天格外冷")).toBeTruthy();
  });

  it("leaves a blank speaker slot as plain text instead of a dialogue row", () => {
    // speaker 位空白不构成规范行（同后端：dialogue utterance 必须带非空 speaker）
    renderScript("镜头1：中景。\n@[ ]：{我来了}");
    expect(screen.getByText(/\{我来了\}/)).toBeTruthy();
  });

  it("leaves a line mixing dialogue into description as plain text", () => {
    renderScript("镜头1：@张三 笑着说 {我来了}。");
    expect(screen.getByText(/\{我来了\}/)).toBeTruthy();
  });
});
