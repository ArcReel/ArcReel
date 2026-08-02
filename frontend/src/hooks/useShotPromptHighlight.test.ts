import { describe, it, expect } from "vitest";
import { tokenizePrompt, toScriptLines, type MentionLookup, type Token } from "./useShotPromptHighlight";

const LOOKUP: MentionLookup = {
  主角: "character",
  张三: "character",
  "角色甲（成年）": "character",
  角色乙: "character",
  酒馆: "scene",
  地点甲·版本A: "scene",
  长剑: "prop",
  载具甲: "prop",
};

function kinds(tokens: Token[]): string[] {
  return tokens.map((t) => (t.kind === "mention" ? `mention:${t.assetKind}` : t.kind));
}

describe("tokenizePrompt", () => {
  it("splits a shot header and plain text", () => {
    const t = tokenizePrompt("镜头1：hello world", LOOKUP);
    expect(kinds(t)).toEqual(["shot_header", "text"]);
    expect(t[0].text).toBe("镜头1：");
    expect(t[1].text).toBe("hello world");
  });

  it("resolves mentions against lookup (three types)", () => {
    const t = tokenizePrompt(
      "镜头1：@主角 in @酒馆 with @长剑",
      LOOKUP,
    );
    expect(kinds(t)).toEqual([
      "shot_header",
      "mention:character",
      "text",
      "mention:scene",
      "text",
      "mention:prop",
    ]);
  });

  it("marks unknown names as 'unknown'", () => {
    const t = tokenizePrompt("镜头1：talk to @路人", LOOKUP);
    const mention = t.find((x) => x.kind === "mention");
    expect(mention?.assetKind).toBe("unknown");
    expect(mention?.text).toBe("@路人");
  });

  it("resolves wrapped mentions with punctuation", () => {
    const t = tokenizePrompt(
      "镜头1：@[角色甲（成年）]引导@[角色乙]靠近@[载具甲]区域，移动到@[地点甲·版本A]",
      LOOKUP,
    );
    const mentions = t.filter((x) => x.kind === "mention");
    expect(mentions.map((x) => (x.kind === "mention" ? x.name : ""))).toEqual([
      "角色甲（成年）",
      "角色乙",
      "载具甲",
      "地点甲·版本A",
    ]);
    expect(kinds(t).filter((kind) => kind.startsWith("mention:"))).toEqual([
      "mention:character",
      "mention:character",
      "mention:prop",
      "mention:scene",
    ]);
  });

  it("treats curly-brace wrapped text as plain text", () => {
    const t = tokenizePrompt("镜头1：@{载具甲} 靠近 @[角色甲（成年）]", LOOKUP);
    const mentions = t.filter((x) => x.kind === "mention");
    expect(mentions.map((x) => (x.kind === "mention" ? x.name : ""))).toEqual(["角色甲（成年）"]);
    expect(t.some((x) => x.kind === "text" && x.text.includes("@{载具甲}"))).toBe(true);
  });

  it("handles multi-line with multiple shot headers", () => {
    const t = tokenizePrompt(
      "镜头1：line1\n镜头2：line2 @主角",
      LOOKUP,
    );
    const shotHeaders = t.filter((x) => x.kind === "shot_header");
    expect(shotHeaders).toHaveLength(2);
    expect(shotHeaders[0].text.startsWith("镜头1")).toBe(true);
    expect(shotHeaders[1].text.startsWith("镜头2")).toBe(true);
  });

  // 时长收编到 unit 级后旧 header 退出解析：`Shot N (Xs):` 不再高亮为 header，按普通描述行处理
  it("no longer treats the legacy `Shot N (Xs):` header as a header", () => {
    const t = tokenizePrompt("Shot 1 (3s): hello world", LOOKUP);
    expect(t.some((x) => x.kind === "shot_header")).toBe(false);
  });

  it("no shot header → entire text becomes text + mention tokens", () => {
    const t = tokenizePrompt("hello @主角 world", LOOKUP);
    expect(kinds(t)).toEqual(["text", "mention:character", "text"]);
  });

  it("is tolerant of trailing whitespace and empty prompt", () => {
    expect(tokenizePrompt("", LOOKUP)).toEqual([]);
    const only = tokenizePrompt("   ", LOOKUP);
    expect(only.map((x) => x.text).join("")).toBe("   ");
  });

  it("rejects '@' following a word character (mirrors backend MENTION_RE boundary)", () => {
    // `price@5`: `e` 是 \w 前缀 → `@5` 不算 mention
    // `email a@b`: `a` 是 \w 前缀 → `@b` 不算 mention
    const t = tokenizePrompt("price@5, email a@b", LOOKUP);
    const mentions = t.filter((x) => x.kind === "mention");
    expect(mentions).toHaveLength(0);
  });
});

describe("toScriptLines shot attribution", () => {
  // parse_prompt 把首个 `镜头N：` 之前的引子折进第一个镜头，不另开一镜。
  // 预览若把引子记成「第 0 镜」，就与服务端派生台词列表里的 shot_index 对不上。
  it("attributes a lead-in written before the first header to shot 1", () => {
    const lines = toScriptLines("@[张三]：{先说}\n镜头1：中景\n镜头2：近景", LOOKUP);
    expect(lines.map((l) => l.shotIndex)).toEqual([1, 1, 2]);
  });

  it("starts at shot 1 when the script opens with a header", () => {
    const lines = toScriptLines("镜头1：中景\n镜头2：近景", LOOKUP);
    expect(lines.map((l) => l.shotIndex)).toEqual([1, 2]);
  });

  it("treats a headerless script as a single shot", () => {
    const lines = toScriptLines("@[张三]：{我来了}", LOOKUP);
    expect(lines.map((l) => l.shotIndex)).toEqual([1]);
  });
});
