import { describe, it, expect } from "vitest";
import {
  buildMentionLookup,
  characterReferenceForms,
  extractMentions,
  formatReferenceName,
  lineSpeechMarks,
  normalizeAssetName,
  referenceInitial,
  resolveCharacterForm,
  splitScriptLines,
  splitDerivativeReference,
  splitSpeechLine,
  stripSpeechMarks,
} from "./reference-mentions";
import type { ProjectData } from "@/types";

/** Marker-assertion shorthand: `[speaker, text]`, empty speakers mean voiceover. */
function marks(line: string): [string, string][] {
  return lineSpeechMarks(line).map((mark) => [mark.speaker, mark.text]);
}

/** Predicate inputs come from normalized `splitScriptLines`; direct primitive tests apply identical normalization. */
function normalize(text: string): string {
  return splitScriptLines(text)[0] ?? "";
}
function mkProject(): Pick<ProjectData, "characters" | "scenes" | "props" | "products"> {
  return {
    characters: { 主角: { description: "" }, 张三: { description: "" }, "角色甲（成年）": { description: "" }, 角色乙: { description: "" } },
    scenes: { 酒馆: { description: "" }, "地点甲·版本A": { description: "" } },
    props: { 长剑: { description: "" }, 载具甲: { description: "" }, 道具甲: { description: "" } },
    products: { 水杯: { description: "", brand: "" } },
  };
}

describe("extractMentions", () => {
  it("returns unique mention names in first-occurrence order", () => {
    expect(extractMentions("@a @b @a @c")).toEqual(["a", "b", "c"]);
  });

  it("returns empty list when no mentions", () => {
    expect(extractMentions("plain text")).toEqual([]);
  });

  it("matches CJK characters and underscores", () => {
    expect(extractMentions("@主角 and @张_三")).toEqual(["主角", "张_三"]);
  });

  it("matches wrapped names containing punctuation", () => {
    expect(extractMentions("@[角色甲（成年）] 接近 @[地点甲·版本A]")).toEqual([
      "角色甲（成年）",
      "地点甲·版本A",
    ]);
  });

  it("matches wrapped names adjacent to verbs", () => {
    expect(extractMentions("@[角色甲（成年）]引导@[角色乙]靠近@[载具甲]区域，使用@[道具甲]完成动作")).toEqual([
      "角色甲（成年）",
      "角色乙",
      "载具甲",
      "道具甲",
    ]);
  });

  it("rejects non-ascii legacy mentions to stay aligned with backend", () => {
    expect(extractMentions("@éclair @한글 @张三 @abc_123")).toEqual(["张三", "abc_123"]);
  });

  it("rejects curly-brace wrapped mentions", () => {
    expect(extractMentions("@[角色甲（成年）] 与 @{道具甲}")).toEqual(["角色甲（成年）"]);
  });
});

describe("parser output is normalized", () => {
  // Parser outputs guarantee NFC/BOM-removed canonical names, matching backend text_parser._normalize_source:
  // callers directly compare normalized asset keys, never normalize independently again.
  const nameNfc = "Hiếu".normalize("NFC");
  const nameNfd = "Hiếu".normalize("NFD");
  const BOM = "\uFEFF";

  it("has distinct NFC / NFD byte forms in the fixtures", () => {
    expect(nameNfc).not.toBe(nameNfd);
  });

  it("emits NFC mention names regardless of the encoding written in the text", () => {
    expect(extractMentions(`@[${nameNfd}] 登场`)).toEqual([nameNfc]);
  });

  it("dedupes mentions across NFC / NFD spellings of the same asset", () => {
    expect(extractMentions(`@[${nameNfc}] 与 @[${nameNfd}]`)).toEqual([nameNfc]);
  });

  it("strips wrapped names before lookup and deduplication", () => {
    expect(extractMentions("@[ Hero ] 与 @[Hero] @Hero")).toEqual(["Hero"]);
  });

  it("strips BOM from inside a mention name", () => {
    // Pasted `@[名<BOM>称]`: backends remove BOM at parser entry; failure to mirror would mark names unregistered and split previews/generation.
    expect(extractMentions(`@[张${BOM}三] 抬眼`)).toEqual(["张三"]);
    expect(extractMentions(`${BOM}@[张三] 抬眼`)).toEqual(["张三"]);
  });

  it("reads a dialogue mark despite BOM and NFD", () => {
    expect(marks(normalize(`${BOM}@[张${BOM}三]：{我${BOM}来了}`))).toEqual([["张三", "我来了"]]);
    expect(marks(normalize(`@[${nameNfd}]：{我来了}`))).toEqual([[nameNfc, "我来了"]]);
  });

  it("reads bare braces as voiceover despite BOM", () => {
    expect(marks(normalize(`${BOM}{那年冬天}`))).toEqual([["", "那年冬天"]]);
  });

  it("keeps a BOM-laced speaker slot out of mention extraction", () => {
    // BOM causing frontend token/backend description classification reverses speaker reference-image inclusion.
    expect(extractMentions(`@[张${BOM}三]：{我来了}`)).toEqual([]);
  });

  it("emits normalized script lines", () => {
    expect(splitScriptLines(`${BOM}中景\n@[${nameNfd}]：{我来了}`)).toEqual([
      "中景",
      `@[${nameNfc}]：{我来了}`,
    ]);
  });

  it("resolves a BOM-laced mention against the registered bucket", () => {
    const lookup = buildMentionLookup(mkProject());
    expect(extractMentions(`@[张${BOM}三] 抬眼`).map((name) => lookup[name])).toEqual(["character"]);
  });
});

describe("buildMentionLookup", () => {
  it("normalizes keys and resolves cross-bucket duplicates by stable priority", () => {
    const lookup = buildMentionLookup({
      characters: Object.fromEntries([[" café ", {}], ["__proto__", {}]]),
      scenes: { "cafe\u0301": {} },
      props: { toString: {} },
      products: { Product: {} },
    } as never);

    expect(Object.getPrototypeOf(lookup)).toBeNull();
    expect(lookup["café"]).toBe("character");
    expect(lookup.__proto__).toBe("character");
    expect(lookup.toString).toBe("prop");
    expect(lookup.Product).toBe("product");
  });

  it("gives products priority when a name is present in another bucket", () => {
    const lookup = buildMentionLookup({
      characters: { Shared: {} },
      scenes: {},
      props: {},
      products: { Shared: {} },
    } as never);

    expect(lookup.Shared).toBe("product");
  });
});

describe("normalizeAssetName", () => {
  it("uses Python strip whitespace without removing U+FEFF", () => {
    expect(normalizeAssetName("\u001c\u3000Hero\u00a0")).toBe("Hero");
    expect(normalizeAssetName("\uFEFFHero")).toBe("\uFEFFHero");
  });

  it("does not conflate an asset starting with U+FEFF with the visible name", () => {
    const lookup = buildMentionLookup({
      characters: { "\uFEFFHero": { description: "" } },
      scenes: {},
      props: {},
    } as never);
    expect(Object.hasOwn(lookup, "Hero")).toBe(false);
    expect(lookup["\uFEFFHero"]).toBe("character");
  });
});

describe("MENTION_RE prefix boundary", () => {
  it("ignores email-like prefix", () => {
    expect(extractMentions("contact a@张三")).toEqual([]);
    expect(extractMentions("test@domain.com")).toEqual([]);
    expect(extractMentions("alice@example.com 和 bob@foo.io")).toEqual([]);
    expect(extractMentions("room9@张三")).toEqual([]);
    expect(extractMentions("user123@李四")).toEqual([]);
  });

  it("accepts Chinese prefix", () => {
    expect(extractMentions("你好@张三")).toEqual(["张三"]);
    expect(extractMentions("（对面）@李四")).toEqual(["李四"]);
  });

  it("accepts whitespace / line-start / punctuation prefix", () => {
    expect(extractMentions("@张三")).toEqual(["张三"]);
    expect(extractMentions("之后 @张三")).toEqual(["张三"]);
    expect(extractMentions("Shot 1 (3s):\n@张三")).toEqual(["张三"]);
    expect(extractMentions("台词：@张三")).toEqual(["张三"]);
  });

  it("preserves valid mention next to email-shape prefix", () => {
    expect(extractMentions("contact a@张三 then @李四 shows up")).toEqual(["李四"]);
  });

  it("rejects underscore prefix", () => {
    expect(extractMentions("prefix_@张三")).toEqual([]);
  });

});

describe("inline speech marks", () => {
  it("matches `@[角色]{台词}` with either colon, wrapped or bare, and no separator", () => {
    expect(marks("@[张三]：{我来了}")).toEqual([["张三", "我来了"]]);
    expect(marks("@张三:{我来了}")).toEqual([["张三", "我来了"]]);
    expect(marks("  @[角色甲（成年）] ： {我来了} ")).toEqual([["角色甲（成年）", "我来了"]]);
    expect(marks("@[ 张三 ]：{我来了}")).toEqual([["张三", "我来了"]]);
    expect(marks("@[张三]{我来了}")).toEqual([["张三", "我来了"]]);
  });

  it("reads speech written inline after a description", () => {
    expect(marks("中景，@[张三] 笑着，@[张三]{我来了} 说完转身")).toEqual([["张三", "我来了"]]);
    expect(marks("@[张三]{你来了}@[李四]{我来了}")).toEqual([
      ["张三", "你来了"],
      ["李四", "我来了"],
    ]);
  });

  it("does not guess a speaker from a mention that is not adjacent to the braces", () => {
    expect(marks("他说 @[张三] 转身，屋里传出 {我来了}")).toEqual([["", "我来了"]]);
    expect(marks("@[张三]：{我来了")).toEqual([]);
  });

  it("reads bare braces as voiceover anywhere in the line", () => {
    expect(marks("  {那年冬天格外冷}  ")).toEqual([["", "那年冬天格外冷"]]);
    expect(marks("旁白：{那年冬天}")).toEqual([["", "那年冬天"]]);
  });

  it("splits a line losslessly", () => {
    const line = "@[张三] 推门。@[张三]{我来了}屋里安静。";
    const joined = splitSpeechLine(line)
      .map((part) => (typeof part === "string" ? part : part.raw))
      .join("");
    expect(joined).toBe(line);
  });

  it("keeps speaker slots out of mention extraction", () => {
    // Match backend text_parser.extract_mentions; referencing offscreen speakers visually would coax them onscreen.
    expect(extractMentions("@酒馆 内景。\n@张三：{我来了}\n@张三 抬眼。")).toEqual([
      "酒馆",
      "张三",
    ]);
    expect(extractMentions("@张三：{我来了}")).toEqual([]);
  });

  it("keeps speaker slots out of mentions across lines", () => {
    expect(extractMentions("@酒馆 内景。\n@张三：{我来了}")).toEqual(["酒馆"]);
  });

  it("does not treat a blank speaker slot as a speech mark", () => {
    // Match backend split_speech_line: whitespace-only speakers form no tokens, preventing invalid utterances.
    expect(marks("@[ ]：{我来了}")).toEqual([]);
    expect(extractMentions("@[ ]：{我来了}")).toEqual([""]);
  });

  it("does not fall back to voiceover when the speaker slot is malformed", () => {
    // Authors wrote Someone speaks; silently turning it into voiceover is harder to detect than nonrecognition.
    expect(marks("@[]：{我来了}")).toEqual([]);
  });

  it("does not fall back to voiceover when the separator colon is repeated", () => {
    // Match backend split_speech_line: consume only one separator colon; remaining colons mean non-dialogue.
    expect(marks("@[张三]：：{我来了}")).toEqual([]);
    expect(stripSpeechMarks("@[张三]：：{我来了}")).toBe("@[张三]：：{我来了}");
    expect(marks("门开了。@[张三]:: {我来了}")).toEqual([]);
  });

  it("still binds the speaker across a single separator colon", () => {
    expect(marks("@[张三]：{我来了}")).toEqual([["张三", "我来了"]]);
    expect(marks("@[张三] : {我来了}")).toEqual([["张三", "我来了"]]);
  });

  it("counts the unit separator as inline whitespace like Python", () => {
    // JS `\s` excludes U+001F, unlike Python str.isspace(); omitting this character
    // binds speakers server-side but derives image references frontend-side.
    expect(marks("@[张三]\u001f{我来了}")).toEqual([["张三", "我来了"]]);
    expect(extractMentions("@[张三]\u001f{我来了}")).toEqual([]);
  });

  it("does not read nested braces as one mark", () => {
    // Match backend split_speech_line: outer `{` forms no token; resume scanning inside.
    expect(marks("{外层 {内层}}")).toEqual([["", "内层"]]);
  });

  it("does not treat blank braces as an utterance", () => {
    // Match backend nonempty utterance text; empty dialogue derives nothing.
    expect(marks("{}")).toEqual([]);
    expect(marks("{   }")).toEqual([]);
    expect(marks("@[张三]：{}")).toEqual([]);
  });

  it("keeps mentions written outside the marks in the reference derivation", () => {
    expect(extractMentions("@[酒馆] 内景。@[张三]{我来了}")).toEqual(["酒馆"]);
    expect(extractMentions("@[张三] 推门。@[张三]{我来了}")).toEqual(["张三"]);
  });
});

describe("splitScriptLines", () => {
  // Match Python str.splitlines() casewise: final breaks add no lines, interior empty lines remain.
  it.each([
    ["", []],
    ["a", ["a"]],
    ["a\n", ["a"]],
    ["a\n\n", ["a", ""]],
    ["a\n\nb", ["a", "", "b"]],
    ["\n", [""]],
    ["中景\r\n", ["中景"]],
    ["中景 ", ["中景"]],
  ])("splits %j the way splitlines() does", (input, expected) => {
    expect(splitScriptLines(input)).toEqual(expected);
  });
});

describe("@[角色/衍生]", () => {
  const project = {
    characters: { 阿岚: { derivatives: { 战斗装: {}, 便装: {} } }, 青禾: {} },
    scenes: { 茶楼: {} },
    props: {},
    products: {},
  } as never;

  it("splits a reference name into base and derivative", () => {
    expect(splitDerivativeReference("阿岚")).toEqual(["阿岚", ""]);
    expect(splitDerivativeReference("阿岚/战斗装")).toEqual(["阿岚", "战斗装"]);
  });

  it("registers every derivative under the character namespace", () => {
    const lookup = buildMentionLookup(project);

    expect(lookup["阿岚/战斗装"]).toBe("character");
    expect(lookup["阿岚"]).toBe("character");
    expect(Object.hasOwn(lookup, "阿岚/夜行衣")).toBe(false);
    expect(Object.hasOwn(lookup, "青禾/战斗装")).toBe(false);
  });

  it("binds the speaker slot to the base character and keeps the derivative", () => {
    expect(lineSpeechMarks("@[阿岚/战斗装]{我来了}")).toEqual([
      { speaker: "阿岚", derivative: "战斗装", text: "我来了", raw: "@[阿岚/战斗装]{我来了}" },
    ]);
  });

  it("does not treat a speaker slot without a base name as a mark", () => {
    expect(marks("@[/战斗装]{我来了}")).toEqual([]);
  });

  it("keeps the derivative mention out of the reference images when it only speaks", () => {
    expect(extractMentions("@[阿岚/战斗装]{我来了}")).toEqual([]);
    expect(extractMentions("@[阿岚/战斗装] 推门")).toEqual(["阿岚/战斗装"]);
  });

  it("lists every form with its ontology first", () => {
    const characters = {
      阿岚: { description: "", derivatives: { 战斗装: { description: "" }, 便装: { description: "" } } },
      青禾: { description: "" },
    };

    expect(characterReferenceForms(characters).map((form) => form.name)).toEqual([
      "阿岚",
      "阿岚/战斗装",
      "阿岚/便装",
      "青禾",
    ]);
  });

  it("resolves a form to its own entry, and an unregistered one to undefined", () => {
    const characters = {
      阿岚: {
        description: "本体",
        character_sheet: "characters/阿岚.png",
        derivatives: { 战斗装: { description: "重甲", character_sheet: "characters/derivatives/阿岚/战斗装.png" } },
      },
    };

    expect(resolveCharacterForm(characters, "阿岚/战斗装")?.asset).toEqual({
      description: "重甲",
      character_sheet: "characters/derivatives/阿岚/战斗装.png",
    });
    expect(resolveCharacterForm(characters, "阿岚")?.asset.character_sheet).toBe("characters/阿岚.png");
    expect(resolveCharacterForm(characters, "阿岚/夜行衣")).toBeUndefined();
  });

  it("spaces out the separator for display only", () => {
    expect(formatReferenceName("阿岚/战斗装")).toBe("阿岚 / 战斗装");
    expect(formatReferenceName("阿岚")).toBe("阿岚");
  });

  it("takes the placeholder initial from the derivative so sibling forms differ", () => {
    expect(referenceInitial("阿岚/战斗装")).toBe("战");
    expect(referenceInitial("阿岚")).toBe("阿");
  });
});
