import { describe, it, expect } from "vitest";
import { tokenizePrompt, type MentionLookup, type Token } from "./useShotPromptHighlight";

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
    const t = tokenizePrompt("Shot 1 (3s): hello world", LOOKUP);
    expect(kinds(t)).toEqual(["shot_header", "text"]);
    expect(t[0].text).toBe("Shot 1 (3s): ");
    expect(t[1].text).toBe("hello world");
  });

  it("resolves mentions against lookup (three types)", () => {
    const t = tokenizePrompt(
      "Shot 1 (3s): @主角 in @酒馆 with @长剑",
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
    const t = tokenizePrompt("Shot 1 (3s): talk to @路人", LOOKUP);
    const mention = t.find((x) => x.kind === "mention");
    expect(mention?.assetKind).toBe("unknown");
    expect(mention?.text).toBe("@路人");
  });

  it("resolves wrapped mentions with punctuation", () => {
    const t = tokenizePrompt(
      "Shot 1 (8s): @[角色甲（成年）]引导@[角色乙]靠近@[载具甲]区域，移动到@[地点甲·版本A]",
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
    const t = tokenizePrompt("Shot 1 (3s): @{载具甲} 靠近 @[角色甲（成年）]", LOOKUP);
    const mentions = t.filter((x) => x.kind === "mention");
    expect(mentions.map((x) => (x.kind === "mention" ? x.name : ""))).toEqual(["角色甲（成年）"]);
    expect(t.some((x) => x.kind === "text" && x.text.includes("@{载具甲}"))).toBe(true);
  });

  it("handles multi-line with multiple shot headers", () => {
    const t = tokenizePrompt(
      "Shot 1 (3s): line1\nShot 2 (5s): line2 @主角",
      LOOKUP,
    );
    const shotHeaders = t.filter((x) => x.kind === "shot_header");
    expect(shotHeaders).toHaveLength(2);
    expect(shotHeaders[0].text.startsWith("Shot 1")).toBe(true);
    expect(shotHeaders[1].text.startsWith("Shot 2")).toBe(true);
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
