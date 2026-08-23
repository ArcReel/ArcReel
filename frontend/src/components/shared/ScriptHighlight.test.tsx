import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ScriptHighlight } from "./ScriptHighlight";
import type { MentionLookup } from "@/hooks/useUnitPromptHighlight";

const LOOKUP: MentionLookup = { 张三: "character", 酒馆: "scene", 长剑: "prop" };

function renderScript(text: string) {
  const { container } = render(<ScriptHighlight text={text} lookup={LOOKUP} />);
  return container;
}

describe("ScriptHighlight", () => {
  it("colors mentions by asset kind and flags unregistered ones", () => {
    const container = renderScript("@张三 在 @酒馆 举起 @长剑，@王五 旁观。");
    const classFor = (name: string) =>
      [...container.querySelectorAll("span")].find((el) => el.textContent === `@${name}`)?.className ?? "";
    expect(classFor("张三")).toContain("sky");
    expect(classFor("酒馆")).toContain("emerald");
    expect(classFor("长剑")).toContain("amber");
    expect(classFor("王五")).toContain("red");
  });

  it("renders a lone dialogue mark as speaker + spoken text, not raw syntax", () => {
    renderScript("中景。\n@[张三]：{我来了}");
    expect(screen.getByText("张三")).toBeInTheDocument();
    expect(screen.getByText("我来了")).toBeInTheDocument();
    // 花括号与冒号是书写语法，解析视图里不再出现
    expect(screen.queryByText(/\{我来了\}/)).not.toBeInTheDocument();
  });

  it("labels a bare braces line as voiceover", () => {
    renderScript("中景。\n{那年冬天格外冷}");
    expect(screen.getByText("画外音")).toBeInTheDocument();
    expect(screen.getByText("那年冬天格外冷")).toBeInTheDocument();
  });

  // 正文没有行前缀语法：写成前缀样子的文字就是描述文字，逐字照原样呈现。
  it("treats prefix-shaped text as ordinary description", () => {
    const container = renderScript("镜头1：中景。");
    const rows = [...container.querySelectorAll<HTMLElement>(":scope > div")];
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent(/^镜头1：中景。$/);
    expect(rows[0].className).not.toContain("border-l-2");
  });

  it("leaves a blank speaker slot as plain text instead of a dialogue row", () => {
    // speaker 位空白不成记号（同后端：dialogue utterance 必须带非空 speaker）
    renderScript("中景。\n@[ ]：{我来了}");
    expect(screen.getByText(/\{我来了\}/)).toBeInTheDocument();
  });

  it("leaves blank braces as plain text instead of an empty utterance", () => {
    renderScript("中景。\n{}");
    expect(screen.getByText("{}")).toBeInTheDocument();
    expect(screen.queryByText("画外音")).not.toBeInTheDocument();
  });

  it("highlights an inline speech mark in place, keeping the line's own wording", () => {
    // 记号与描述混写的行按描述行渲染，记号在行内就地着色——预览要与作者写的那一行对得上。
    const container = renderScript("@张三 推门。@[张三]{我来了}");
    const mark = [...container.querySelectorAll("span")].find((el) => el.textContent === "@[张三]{我来了}");
    expect(mark).toBeTruthy();
    expect(mark?.getAttribute("title")).toBe("张三");
  });

  it("highlights an inline voiceover mark with the voiceover label as its title", () => {
    const container = renderScript("门开了。{那年冬天格外冷}");
    const mark = [...container.querySelectorAll("span")].find((el) => el.textContent === "{那年冬天格外冷}");
    expect(mark?.getAttribute("title")).toBe("画外音");
  });

  it("calls renderAfterLine once per source line", () => {
    const calls: number[] = [];
    const text = "@[张三]：{我来了}\n中景。";
    render(
      <ScriptHighlight
        text={text}
        lookup={LOOKUP}
        renderAfterLine={(sourceLine) => {
          calls.push(sourceLine);
          return null;
        }}
      />,
    );
    expect(calls).toEqual([0, 1]);
  });
});
