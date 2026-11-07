/**
 * Legacy transition banner: after character-reference audio updates, one dismissible canvas-top banner notes clips generated
 * before settings. Aggregate by canvas without clip-level badges.
 *
 * Derive counts on read without persisting old-clip sets, using two automatic timestamps:
 * - Character.voice_updated_at: stamp every reference_audio change, including clearing/importing
 * - GeneratedAssets.video_generated_at: stamp completed video generation; absent historical clips count as
 *   preceding any setting
 *
 * Dismissal uses Character.voice_notice_dismissed_at timestamp, not boolean, meaning acknowledged
 * voice version. Write the character's then-current voice_updated_at unchanged (both backend-clock values,
 * immune to client-clock drift/ISO format differences). Later voice_updated_at means a new change,
 * reappearing without extra reset logic. Aggregate by episode canvas units, writing dismissal separately
 * for every character actually contributing to counts.
 */
import type { Character, CharacterVoiceBinding } from "@/types/project";
import { DEFAULT_CHARACTER_VOICE_BINDING } from "@/types/project";
import { dialogueSpeakers, normalizeAssetName } from "@/utils/reference-mentions";

/**
 * Minimal unit shape for computeVoiceLegacyNotice, supporting narration/drama across
 * all `ReferenceVideoUnit` content types. Completed units
 * stamp `video_generated_at` through shared `apply_unit_video_assets` persistence,
 * making banner decisions independent of canvas type.
 */
export interface VoiceNoticeUnit {
  unit_id: string;
  /** Unit body; derive speaking characters from dialogue-marker speaker positions, sharing execution audio-binding logic. */
  text?: string | null;
  generated_assets?: { status?: string | null; video_generated_at?: string | null } | null;
}

export interface VoiceLegacyNotice {
  /** Count of clips predating settings and not covered by current dismissals. */
  count: number;
  /** Actually counted character names, each requiring dismissed_at writeback on dismissal. */
  characterNames: string[];
}

const EMPTY_NOTICE: VoiceLegacyNotice = { count: 0, characterNames: [] };

/** Timestamps differ in precision/format (microseconds+offset versus restored second-resolution Z); compare parsed instants. */
function toEpochMs(iso: string): number {
  return new Date(iso).getTime();
}

/**
 * Independently testable pure function, without store/hook dependencies.
 *
 * `voiceBinding` prompt (default) always returns empty: that path binds no reference audio, so changes do not affect
 * generated voices and banners would point to currently ineffective settings.
 */
export function computeVoiceLegacyNotice(
  units: readonly VoiceNoticeUnit[],
  characters: Record<string, Character>,
  voiceBinding: CharacterVoiceBinding = DEFAULT_CHARACTER_VOICE_BINDING,
): VoiceLegacyNotice {
  if (voiceBinding !== "reference_audio") return EMPTY_NOTICE;
  const staleUnitIds = new Set<string>();
  const characterNames = new Set<string>();
  // characters keys/body names may use NFC/NFD; normalize before lookup, matching
  // `utils/reference-mentions.ts` coordinates. Collect actual bucket keys for name,
  // because consumers directly index `characters[name]` for dismissal writes.
  const normalizedCharacters = new Map<string, { key: string; character: Character }>();
  for (const [key, character] of Object.entries(characters)) {
    normalizedCharacters.set(normalizeAssetName(key), { key, character });
  }

  for (const unit of units) {
    if (unit.generated_assets?.status !== "completed") continue;
    const videoGeneratedAt = unit.generated_assets?.video_generated_at;

    // Inspect dialogue speakers only: voice affects spoken dialogue, not merely appearing characters;
    // silent-character voice changes do not outdate generated video. Missing bodies are valid at validation (migration shells may be empty),
    // treated as empty strings so one unit cannot crash the whole reference-to-video canvas.
    for (const name of dialogueSpeakers(unit.text ?? "")) {
      const found = normalizedCharacters.get(name);
      if (!found) continue;
      const { key: characterKey, character } = found;
      const voiceUpdatedAt = character.voice_updated_at;
      if (!voiceUpdatedAt) continue;
      const voiceUpdatedMs = toEpochMs(voiceUpdatedAt);

      const dismissedAt = character.voice_notice_dismissed_at;
      if (dismissedAt && toEpochMs(dismissedAt) >= voiceUpdatedMs) continue;

      const isStale = !videoGeneratedAt || toEpochMs(videoGeneratedAt) < voiceUpdatedMs;
      if (!isStale) continue;

      staleUnitIds.add(unit.unit_id);
      characterNames.add(characterKey);
    }
  }

  if (staleUnitIds.size === 0) return EMPTY_NOTICE;
  return { count: staleUnitIds.size, characterNames: [...characterNames] };
}
