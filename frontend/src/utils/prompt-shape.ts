import type { ImagePrompt, VideoPrompt } from "@/types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/**
 * Strict guards: complete ImagePrompt requires scene + composition.{shot_type, lighting, ambiance}.
 * Partial shapes (scene only/missing composition fields) use string fallback, avoiding structured-editor crashes
 * on undefined fields.
 */
export function isStructuredImagePrompt(value: unknown): value is ImagePrompt {
  if (!isRecord(value) || typeof value.scene !== "string") return false;
  const composition = value.composition;
  if (!isRecord(composition)) return false;
  return (
    typeof composition.shot_type === "string" &&
    typeof composition.lighting === "string" &&
    typeof composition.ambiance === "string"
  );
}

/**
 * Strict guards: VideoPrompt requires action + camera_motion + ambiance_audio; optional dialogue,
 * when supplied, must be {speaker, line} arrays.
 */
export function isStructuredVideoPrompt(value: unknown): value is VideoPrompt {
  if (
    !isRecord(value) ||
    typeof value.action !== "string" ||
    typeof value.camera_motion !== "string" ||
    typeof value.ambiance_audio !== "string"
  ) {
    return false;
  }
  const dialogue = value.dialogue;
  if (dialogue === undefined) return true;
  if (!Array.isArray(dialogue)) return false;
  return dialogue.every(
    (item) =>
      isRecord(item) &&
      typeof item.speaker === "string" &&
      typeof item.line === "string",
  );
}

/** Empty structures when switching text to structured forms; never parse old text, leave fields for creator entry. */
export function emptyImagePrompt(): ImagePrompt {
  return { scene: "", composition: { shot_type: "Medium Shot", lighting: "", ambiance: "" } };
}

/**
 * As above; drama speech lives in storyboard utterances, so video_prompt omits dialogue (backend
 * DramaVideoPrompt strips it on read with extra="forbid"); drama branches assert the same type.
 */
export function emptyVideoPrompt(isDrama: boolean): VideoPrompt {
  const base = { action: "", camera_motion: "Static" as const, ambiance_audio: "" };
  return isDrama ? (base as VideoPrompt) : { ...base, dialogue: [] };
}
