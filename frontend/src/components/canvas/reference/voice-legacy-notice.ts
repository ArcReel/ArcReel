import type { Character, CharacterVoiceBinding } from "@/types/project";
import { DEFAULT_CHARACTER_VOICE_BINDING } from "@/types/project";
import { dialogueSpeakers, normalizeAssetName } from "@/utils/reference-mentions";

export interface VoiceNoticeUnit {
  unit_id: string;
  text?: string | null;
  generated_assets?: { status?: string | null; video_generated_at?: string | null } | null;
}

export interface VoiceLegacyNotice {
  count: number;
  characterNames: string[];
}

const EMPTY_NOTICE: VoiceLegacyNotice = { count: 0, characterNames: [] };

function toEpochMs(iso: string): number {
  return new Date(iso).getTime();
}

export function computeVoiceLegacyNotice(
  units: readonly VoiceNoticeUnit[],
  characters: Record<string, Character>,
  voiceBinding: CharacterVoiceBinding = DEFAULT_CHARACTER_VOICE_BINDING,
): VoiceLegacyNotice {
  if (voiceBinding !== "reference_audio") return EMPTY_NOTICE;
  const staleUnitIds = new Set<string>();
  const characterNames = new Set<string>();
  const normalizedCharacters = new Map<string, { key: string; character: Character }>();
  for (const [key, character] of Object.entries(characters)) {
    normalizedCharacters.set(normalizeAssetName(key), { key, character });
  }

  for (const unit of units) {
    if (unit.generated_assets?.status !== "completed") continue;
    const videoGeneratedAt = unit.generated_assets?.video_generated_at;

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
