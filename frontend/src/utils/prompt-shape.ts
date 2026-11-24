import type { ImagePrompt, VideoPrompt } from "@/types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

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

export function emptyImagePrompt(): ImagePrompt {
  return { scene: "", composition: { shot_type: "Medium Shot", lighting: "", ambiance: "" } };
}

export function emptyVideoPrompt(isDrama: boolean): VideoPrompt {
  const base = { action: "", camera_motion: "Static" as const, ambiance_audio: "" };
  return isDrama ? (base as VideoPrompt) : { ...base, dialogue: [] };
}
