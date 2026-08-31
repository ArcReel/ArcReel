import type { ImagePrompt, VideoPrompt } from "@/types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/**
 * 严格守卫：完整 ImagePrompt 必须含 scene + composition.{shot_type, lighting, ambiance}。
 * 部分形态（仅 scene 或 composition 缺字段）会落到 string fallback，避免结构化编辑器渲染时
 * 访问 undefined 字段崩溃。
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
 * 严格守卫：VideoPrompt 必须含 action + camera_motion + ambiance_audio；dialogue 可省略，
 * 但若提供必须是 {speaker, line} 数组。
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

/** 文本形态切回结构化时的空结构：不解析原文本，字段留空由创作者重填。 */
export function emptyImagePrompt(): ImagePrompt {
  return { scene: "", composition: { shot_type: "Medium Shot", lighting: "", ambiance: "" } };
}

/**
 * 同上；drama 的口播由分镜级 utterances 承载，其 video_prompt 不带 dialogue（后端
 * DramaVideoPrompt 在 extra="forbid" 下读时剥离该键），故 drama 分支断言到同一类型。
 */
export function emptyVideoPrompt(isDrama: boolean): VideoPrompt {
  const base = { action: "", camera_motion: "Static" as const, ambiance_audio: "" };
  return isDrama ? (base as VideoPrompt) : { ...base, dialogue: [] };
}
