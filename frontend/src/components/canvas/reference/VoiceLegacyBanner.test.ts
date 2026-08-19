import { describe, it, expect } from "vitest";
import { computeVoiceLegacyNotice } from "./VoiceLegacyBanner";
import type { ReferenceVideoUnit, UnitGeneratedAssets } from "@/types/reference-video";
import type { Character } from "@/types/project";

function ga(overrides: Partial<UnitGeneratedAssets>): UnitGeneratedAssets {
  return {
    storyboard_image: null,
    storyboard_last_image: null,
    grid_id: null,
    grid_cell_index: null,
    video_clip: null,
    video_uri: null,
    status: "completed",
    video_generated_at: null,
    ...overrides,
  };
}

function unit(id: string, characterName: string, generatedAssets: UnitGeneratedAssets): ReferenceVideoUnit {
  return {
    unit_id: id,
    text: `@[${characterName}] 出场`,
    duration_seconds: 5,
    transition_to_next: "cut",
    note: null,
    generated_assets: generatedAssets,
  };
}

const character = (overrides: Partial<Character>): Character => ({ description: "", ...overrides });

describe("computeVoiceLegacyNotice", () => {
  it("returns zero when no character has voice_updated_at", () => {
    const units = [unit("E1U1", "王", ga({ video_generated_at: null }))];
    const result = computeVoiceLegacyNotice(units, { 王: character({}) });
    expect(result).toEqual({ count: 0, characterNames: [] });
  });

  it("counts a completed unit with no video_generated_at as legacy (predates the field)", () => {
    const units = [unit("E1U1", "王", ga({ video_generated_at: null }))];
    const characters = { 王: character({ voice_updated_at: "2026-01-02T00:00:00Z" }) };
    expect(computeVoiceLegacyNotice(units, characters)).toEqual({ count: 1, characterNames: ["王"] });
  });

  it("resolves a mention across NFC/NFD encoding mismatch and reports the registered key", () => {
    const nameNfc = "Hiếu".normalize("NFC");
    const nameNfd = "Hiếu".normalize("NFD");
    expect(nameNfc).not.toBe(nameNfd);
    const units = [unit("E1U1", nameNfc, ga({ video_generated_at: null }))];
    const characters = { [nameNfd]: character({ voice_updated_at: "2026-01-02T00:00:00Z" }) };
    expect(computeVoiceLegacyNotice(units, characters)).toEqual({ count: 1, characterNames: [nameNfd] });
  });

  it("counts a unit generated before voice_updated_at", () => {
    const units = [unit("E1U1", "王", ga({ video_generated_at: "2026-01-01T00:00:00Z" }))];
    const characters = { 王: character({ voice_updated_at: "2026-01-02T00:00:00Z" }) };
    expect(computeVoiceLegacyNotice(units, characters)).toEqual({ count: 1, characterNames: ["王"] });
  });

  it("excludes a unit generated after voice_updated_at", () => {
    const units = [unit("E1U1", "王", ga({ video_generated_at: "2026-01-03T00:00:00Z" }))];
    const characters = { 王: character({ voice_updated_at: "2026-01-02T00:00:00Z" }) };
    expect(computeVoiceLegacyNotice(units, characters)).toEqual({ count: 0, characterNames: [] });
  });

  it("excludes units that are not completed", () => {
    const units = [unit("E1U1", "王", ga({ status: "pending", video_generated_at: null }))];
    const characters = { 王: character({ voice_updated_at: "2026-01-02T00:00:00Z" }) };
    expect(computeVoiceLegacyNotice(units, characters)).toEqual({ count: 0, characterNames: [] });
  });

  it("excludes units already covered by a later dismissal", () => {
    const units = [unit("E1U1", "王", ga({ video_generated_at: null }))];
    const characters = {
      王: character({ voice_updated_at: "2026-01-02T00:00:00Z", voice_notice_dismissed_at: "2026-01-03T00:00:00Z" }),
    };
    expect(computeVoiceLegacyNotice(units, characters)).toEqual({ count: 0, characterNames: [] });
  });

  it("reappears once voice_updated_at moves past a stale dismissal", () => {
    const units = [unit("E1U1", "王", ga({ video_generated_at: null }))];
    const characters = {
      王: character({ voice_updated_at: "2026-02-01T00:00:00Z", voice_notice_dismissed_at: "2026-01-03T00:00:00Z" }),
    };
    expect(computeVoiceLegacyNotice(units, characters)).toEqual({ count: 1, characterNames: ["王"] });
  });

  it("dedupes unit count when multiple mentioned characters are both stale", () => {
    const u = unit("E1U1", "王", ga({ video_generated_at: null }));
    u.text += "，@[李] 跟上";
    const characters = {
      王: character({ voice_updated_at: "2026-01-02T00:00:00Z" }),
      李: character({ voice_updated_at: "2026-01-02T00:00:00Z" }),
    };
    const result = computeVoiceLegacyNotice([u], characters);
    expect(result.count).toBe(1);
    expect(result.characterNames.sort()).toEqual(["李", "王"]);
  });

  it("ignores names that are not registered characters", () => {
    const u = unit("E1U1", "路人", ga({ video_generated_at: null }));
    const characters = { 王: character({ voice_updated_at: "2026-01-02T00:00:00Z" }) };
    expect(computeVoiceLegacyNotice([u], characters)).toEqual({ count: 0, characterNames: [] });
  });

  // 说话人位不进参考图，同 extract_mentions：只在花括号前出现的角色只提供声音、不入画。
  it("ignores a character that only occupies a speaker slot", () => {
    const u = unit("E1U1", "王", ga({ video_generated_at: null }));
    u.text = "@[王]{我来了}";
    const characters = { 王: character({ voice_updated_at: "2026-01-02T00:00:00Z" }) };
    expect(computeVoiceLegacyNotice([u], characters)).toEqual({ count: 0, characterNames: [] });
  });

  it("does not throw when a unit body is empty (迁移问题壳的合法缺省状态)", () => {
    const u = unit("E1U1", "王", ga({ video_generated_at: null }));
    u.text = "";
    const characters = { 王: character({ voice_updated_at: "2026-01-02T00:00:00Z" }) };
    expect(() => computeVoiceLegacyNotice([u], characters)).not.toThrow();
    expect(computeVoiceLegacyNotice([u], characters)).toEqual({ count: 0, characterNames: [] });
  });

  it("compares timestamps as parsed instants across differing ISO precision/format", () => {
    // 还原历史版本的 video_generated_at 是秒级 "Z" 格式；正常声音更新是微秒级 "+00:00"。
    // 同一秒内，字符串比较会把 "...10Z" 误判为晚于 "...10.500000+00:00"，导致漏判 stale。
    const units = [unit("E1U1", "王", ga({ video_generated_at: "2026-01-02T00:00:10Z" }))];
    const characters = { 王: character({ voice_updated_at: "2026-01-02T00:00:10.500000+00:00" }) };
    expect(computeVoiceLegacyNotice(units, characters)).toEqual({ count: 1, characterNames: ["王"] });
  });
});
