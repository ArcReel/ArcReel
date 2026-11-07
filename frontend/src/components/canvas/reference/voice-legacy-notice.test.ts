import { describe, it, expect } from "vitest";
import { computeVoiceLegacyNotice } from "./voice-legacy-notice";
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

// Voice affects only dialogue spoken by this character, so fixtures include speech; appearing onscreen does not determine voice staleness.
function unit(id: string, characterName: string, generatedAssets: UnitGeneratedAssets): ReferenceVideoUnit {
  return {
    unit_id: id,
    text: `@[${characterName}] 出场。@[${characterName}]{我来了}`,
    duration_seconds: 5,
    note: null,
    generated_assets: generatedAssets,
  };
}

const character = (overrides: Partial<Character>): Character => ({ description: "", ...overrides });

// Banner exists only for reference-audio binding; all cases use that mode except the dedicated gating group.
const notice = (units: readonly ReferenceVideoUnit[], characters: Record<string, Character>) =>
  computeVoiceLegacyNotice(units, characters, "reference_audio");

describe("computeVoiceLegacyNotice", () => {
  it("returns zero when no character has voice_updated_at", () => {
    const units = [unit("E1U1", "王", ga({ video_generated_at: null }))];
    const result = notice(units, { 王: character({}) });
    expect(result).toEqual({ count: 0, characterNames: [] });
  });

  it("counts a completed unit with no video_generated_at as legacy (predates the field)", () => {
    const units = [unit("E1U1", "王", ga({ video_generated_at: null }))];
    const characters = { 王: character({ voice_updated_at: "2026-01-02T00:00:00Z" }) };
    expect(notice(units, characters)).toEqual({ count: 1, characterNames: ["王"] });
  });

  it("resolves a mention across NFC/NFD encoding mismatch and reports the registered key", () => {
    const nameNfc = "Hiếu".normalize("NFC");
    const nameNfd = "Hiếu".normalize("NFD");
    expect(nameNfc).not.toBe(nameNfd);
    const units = [unit("E1U1", nameNfc, ga({ video_generated_at: null }))];
    const characters = { [nameNfd]: character({ voice_updated_at: "2026-01-02T00:00:00Z" }) };
    expect(notice(units, characters)).toEqual({ count: 1, characterNames: [nameNfd] });
  });

  it("counts a unit generated before voice_updated_at", () => {
    const units = [unit("E1U1", "王", ga({ video_generated_at: "2026-01-01T00:00:00Z" }))];
    const characters = { 王: character({ voice_updated_at: "2026-01-02T00:00:00Z" }) };
    expect(notice(units, characters)).toEqual({ count: 1, characterNames: ["王"] });
  });

  it("excludes a unit generated after voice_updated_at", () => {
    const units = [unit("E1U1", "王", ga({ video_generated_at: "2026-01-03T00:00:00Z" }))];
    const characters = { 王: character({ voice_updated_at: "2026-01-02T00:00:00Z" }) };
    expect(notice(units, characters)).toEqual({ count: 0, characterNames: [] });
  });

  it("excludes units that are not completed", () => {
    const units = [unit("E1U1", "王", ga({ status: "pending", video_generated_at: null }))];
    const characters = { 王: character({ voice_updated_at: "2026-01-02T00:00:00Z" }) };
    expect(notice(units, characters)).toEqual({ count: 0, characterNames: [] });
  });

  it("excludes units already covered by a later dismissal", () => {
    const units = [unit("E1U1", "王", ga({ video_generated_at: null }))];
    const characters = {
      王: character({ voice_updated_at: "2026-01-02T00:00:00Z", voice_notice_dismissed_at: "2026-01-03T00:00:00Z" }),
    };
    expect(notice(units, characters)).toEqual({ count: 0, characterNames: [] });
  });

  it("reappears once voice_updated_at moves past a stale dismissal", () => {
    const units = [unit("E1U1", "王", ga({ video_generated_at: null }))];
    const characters = {
      王: character({ voice_updated_at: "2026-02-01T00:00:00Z", voice_notice_dismissed_at: "2026-01-03T00:00:00Z" }),
    };
    expect(notice(units, characters)).toEqual({ count: 1, characterNames: ["王"] });
  });

  it("dedupes unit count when multiple speaking characters are both stale", () => {
    const u = unit("E1U1", "王", ga({ video_generated_at: null }));
    u.text += "\n@[李]{我也在}";
    const characters = {
      王: character({ voice_updated_at: "2026-01-02T00:00:00Z" }),
      李: character({ voice_updated_at: "2026-01-02T00:00:00Z" }),
    };
    const result = notice([u], characters);
    expect(result.count).toBe(1);
    expect(result.characterNames.sort()).toEqual(["李", "王"]);
  });

  it("ignores names that are not registered characters", () => {
    const u = unit("E1U1", "路人", ga({ video_generated_at: null }));
    const characters = { 王: character({ voice_updated_at: "2026-01-02T00:00:00Z" }) };
    expect(notice([u], characters)).toEqual({ count: 0, characterNames: [] });
  });

  // Speakers do not enter image references but are voice targets: changing an offscreen speaker's voice still outdates video.
  it("counts a character that only occupies a speaker slot", () => {
    const u = unit("E1U1", "王", ga({ video_generated_at: null }));
    u.text = "@[王]{我来了}";
    const characters = { 王: character({ voice_updated_at: "2026-01-02T00:00:00Z" }) };
    expect(notice([u], characters)).toEqual({ count: 1, characterNames: ["王"] });
  });

  it("ignores a character that is only referenced visually", () => {
    const u = unit("E1U1", "王", ga({ video_generated_at: null }));
    u.text = "@[王] 推门进屋";
    const characters = { 王: character({ voice_updated_at: "2026-01-02T00:00:00Z" }) };
    expect(notice([u], characters)).toEqual({ count: 0, characterNames: [] });
  });

  it("does not throw when a unit body is empty (迁移问题壳的合法缺省状态)", () => {
    const u = unit("E1U1", "王", ga({ video_generated_at: null }));
    u.text = "";
    const characters = { 王: character({ voice_updated_at: "2026-01-02T00:00:00Z" }) };
    expect(() => notice([u], characters)).not.toThrow();
    expect(notice([u], characters)).toEqual({ count: 0, characterNames: [] });
  });

  it("compares timestamps as parsed instants across differing ISO precision/format", () => {
    // Restored video_generated_at uses second-resolution "Z"; normal voice updates use microsecond "+00:00".
    // In one second, string comparison misorders "...10Z" after "...10.500000+00:00", missing stale status.
    const units = [unit("E1U1", "王", ga({ video_generated_at: "2026-01-02T00:00:10Z" }))];
    const characters = { 王: character({ voice_updated_at: "2026-01-02T00:00:10.500000+00:00" }) };
    expect(notice(units, characters)).toEqual({ count: 1, characterNames: ["王"] });
  });

  describe("角色声音绑定方式门控", () => {
    const units = [unit("E1U1", "王", ga({ video_generated_at: "2026-01-01T00:00:00Z" }))];
    const characters = { 王: character({ voice_updated_at: "2026-01-02T00:00:00Z" }) };

    it("提示词软约束档下恒为空——该档不挂参考音频，换了也不改变已生成视频的声音", () => {
      expect(computeVoiceLegacyNotice(units, characters, "prompt")).toEqual({ count: 0, characterNames: [] });
    });

    it("绑定方式缺省时按默认档（提示词软约束）处理", () => {
      expect(computeVoiceLegacyNotice(units, characters)).toEqual({ count: 0, characterNames: [] });
    });
  });
});
