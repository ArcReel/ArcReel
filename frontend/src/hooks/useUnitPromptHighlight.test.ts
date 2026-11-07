import { describe, it, expect } from "vitest";
import { tokenizePrompt, toScriptLines, type MentionLookup, type Token } from "./useUnitPromptHighlight";
import { extractMentions } from "@/utils/reference-mentions";

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
  it("resolves mentions against lookup (three types)", () => {
    const t = tokenizePrompt("@主角 in @酒馆 with @长剑", LOOKUP);
    expect(kinds(t)).toEqual([
      "mention:character",
      "text",
      "mention:scene",
      "text",
      "mention:prop",
    ]);
  });

  it("marks unknown names as 'unknown'", () => {
    const t = tokenizePrompt("talk to @路人", LOOKUP);
    const mention = t.find((x) => x.kind === "mention");
    expect(mention?.assetKind).toBe("unknown");
    expect(mention?.text).toBe("@路人");
  });

  it("resolves registered products like any other asset kind", () => {
    const t = tokenizePrompt("@水杯 特写", { ...LOOKUP, 水杯: "product" });
    const mention = t.find((x) => x.kind === "mention");
    expect(mention?.assetKind).toBe("product");
  });

  it("resolves wrapped mentions with punctuation", () => {
    const t = tokenizePrompt(
      "@[角色甲（成年）]引导@[角色乙]靠近@[载具甲]区域，移动到@[地点甲·版本A]",
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

  it("normalizes padded wrapped names before lookup and token output", () => {
    const t = tokenizePrompt("@[ 主角 ] 入场", LOOKUP);
    const mention = t.find((x) => x.kind === "mention");
    expect(mention).toMatchObject({ assetKind: "character", name: "主角", text: "@[ 主角 ]" });
  });

  it("does not read curly-brace wrapping as a mention", () => {
    // `@{名称}` is not mention syntax; braces parse as voiceover markers, matching backend split_speech_line.
    const t = tokenizePrompt("@{载具甲} 靠近 @[角色甲（成年）]", LOOKUP);
    const mentions = t.filter((x) => x.kind === "mention");
    expect(mentions.map((x) => (x.kind === "mention" ? x.name : ""))).toEqual(["角色甲（成年）"]);
    expect(t.filter((x) => x.kind === "speech").map((x) => x.text)).toEqual(["{载具甲}"]);
  });

  it("tokenizes an inline speech mark and still concatenates back to the source", () => {
    const text = "@[角色甲（成年）] 推门。@[角色甲（成年）]{我来了}";
    const t = tokenizePrompt(text, LOOKUP);
    expect(t.map((x) => x.text).join("")).toBe(text);
    expect(t.filter((x) => x.kind === "speech")).toEqual([
      {
        kind: "speech",
        text: "@[角色甲（成年）]{我来了}",
        speaker: "角色甲（成年）",
        speakerKind: "character",
      },
    ]);
  });

  // No paragraph-prefix syntax: `镜头1：` and `Shot 1 (3s):` are ordinary description text.
  it("treats prefix-shaped text as plain description", () => {
    for (const src of ["镜头1：hello world", "Shot 1 (3s): hello world", "  镜头１：内景 @主角"]) {
      const t = tokenizePrompt(src, LOOKUP);
      expect(kinds(t).every((kind) => kind === "text" || kind.startsWith("mention:"))).toBe(true);
      expect(t.map((x) => x.text).join("")).toBe(src);
    }
  });

  // Unregistered `toString` follows prototypes to Object.prototype.toString, misclassified as resolved types.
  it("treats prototype-chain names absent from the lookup as unresolved", () => {
    const t = tokenizePrompt("@toString 出场", LOOKUP);
    const mention = t.find((x) => x.kind === "mention");
    expect(mention).toBeDefined();
    expect(mention && "assetKind" in mention && mention.assetKind).toBe("unknown");
  });

  it("splits plain text into text + mention tokens", () => {
    const t = tokenizePrompt("hello @主角 world", LOOKUP);
    expect(kinds(t)).toEqual(["text", "mention:character", "text"]);
  });

  it("is tolerant of trailing whitespace and empty prompt", () => {
    expect(tokenizePrompt("", LOOKUP)).toEqual([]);
    const only = tokenizePrompt("   ", LOOKUP);
    expect(only.map((x) => x.text).join("")).toBe("   ");
  });

  it("rejects '@' following a word character (mirrors backend MENTION_RE boundary)", () => {
    // `price@5`: `e` is \w prefix → `@5` is not a mention
    // `email a@b`: `a` is \w prefix → `@b` is not a mention
    const t = tokenizePrompt("price@5, email a@b", LOOKUP);
    const mentions = t.filter((x) => x.kind === "mention");
    expect(mentions).toHaveLength(0);
  });

  it("resolves a mention typed in NFD against a lookup already normalized to NFC by the caller", () => {
    // MentionLookup requires normalized caller-built keys (type comments above). Here prompt mentions
    // use input-method NFD while lookup is NFC; independent origins require canonical parser outputs
    // sharing coordinates for successful matching.
    const nameNfc = "Hiếu".normalize("NFC");
    const nameNfd = "Hiếu".normalize("NFD");
    expect(nameNfc).not.toBe(nameNfd);
    const lookup: MentionLookup = { [nameNfc]: "character" };
    const t = tokenizePrompt(`@[${nameNfd}] 出场`, lookup);
    const mention = t.find((x) => x.kind === "mention");
    expect(mention?.assetKind).toBe("character");
  });
});

describe("toScriptLines", () => {
  it("emits one line per physical source line", () => {
    const lines = toScriptLines("@[张三] 推门。\n@[张三]：{我来了}\n{风声}", LOOKUP);
    expect(lines.map((l) => l.kind)).toEqual(["text", "dialogue", "voiceover"]);
    expect(lines.map((l) => l.sourceLine)).toEqual([0, 1, 2]);
  });

  it("emits a dialogue speaker typed in NFD as NFC and resolves it against the lookup", () => {
    const nameNfc = "Hiếu".normalize("NFC");
    const nameNfd = "Hiếu".normalize("NFD");
    expect(nameNfc).not.toBe(nameNfd);
    const lookup: MentionLookup = { [nameNfc]: "character" };
    const lines = toScriptLines(`@[${nameNfd}]：{我来了}`, lookup);
    expect(lines).toEqual([
      { kind: "dialogue", sourceLine: 0, speaker: nameNfc, speakerKind: "character", text: "我来了" },
    ]);
  });

  // Backend line parsing strips before classification, retaining indented/trailing-whitespace dialogue;
  // previews downgrading it to descriptions would contradict same-screen server-derived dialogue lists.
  it("keeps a whole-line utterance padded with whitespace on its own line", () => {
    expect(toScriptLines("  @[张三]：{我来了}  ", LOOKUP)).toEqual([
      { kind: "dialogue", sourceLine: 0, speaker: "张三", speakerKind: "character", text: "我来了" },
    ]);
    expect(toScriptLines("\t{风吹过旷野} ", LOOKUP)).toEqual([
      { kind: "voiceover", sourceLine: 0, text: "风吹过旷野" },
    ]);
  });

  // Only mixed description/markers yield `text`; together these tests lock whitespace as nondescription.
  it("keeps a line that mixes description and a speech mark as a text line", () => {
    const lines = toScriptLines("@[张三] 推门。@[张三]{我来了}", LOOKUP);
    expect(lines.map((l) => l.kind)).toEqual(["text"]);
  });

  it("resolves a BOM-laced speaker and renders the name without it", () => {
    // Match backend text_parser: remove BOM at parse entry, aligning speakers/lookup coordinates.
    const lines = toScriptLines(`@[张${"\uFEFF"}三]：{我来了}`, LOOKUP);
    expect(lines).toEqual([
      { kind: "dialogue", sourceLine: 0, speaker: "张三", speakerKind: "character", text: "我来了" },
    ]);
  });
});

describe("unicode line boundaries", () => {
  // Backend Python str.splitlines() recognizes U+2028/U+2029/\x85; frontend splitting only \n would
  // join subsequent dialogue markers to previous lines, counting speakers as image references and diverging derivation paths.
  const LS = "\u2028";

  it("splits on the same boundaries the backend does", () => {
    expect(extractMentions(`@酒馆 内景。${LS}@[张三]：{我来了}`)).toEqual(["酒馆"]);
    expect(extractMentions("@酒馆 内景。\u2029@[张三]：{我来了}")).toEqual(["酒馆"]);
    expect(extractMentions("@酒馆 内景。\x85@[张三]：{我来了}")).toEqual(["酒馆"]);
  });

  it("treats CRLF as one boundary rather than two", () => {
    const lines = toScriptLines("中景\r\n近景", LOOKUP);
    expect(lines.map((l) => l.sourceLine)).toEqual([0, 1]);
  });

  it("keeps tokenizePrompt output concatenable back to the source text", () => {
    const text = `@主角 在场。${LS}@[张三]：{我来了}\r\n结束`;
    expect(tokenizePrompt(text, LOOKUP).map((t) => t.text).join("")).toBe(text);
  });

  it("splits a line that follows a unicode separator", () => {
    const lines = toScriptLines(`@[张三]：{我来了}${LS}近景`, LOOKUP);
    expect(lines.map((l) => l.kind)).toEqual(["dialogue", "text"]);
  });
});
