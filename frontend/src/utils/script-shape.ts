/**
 * 剧本形状分派（与后端 SCRIPT_SHAPES 同口径）：content_mode → 条目 ID / 角色引用字段。
 *
 * 时间线编辑器各组件（列表 / 分屏 / 详情 / 引用区）共用，避免三元映射在多处漂移。
 */

import type { NarrationSegment, DramaScene, AdShot } from "@/types";

export type EditorContentMode = "narration" | "drama" | "ad";
export type ScriptItem = NarrationSegment | DramaScene | AdShot;

export type CharactersField =
  | "characters_in_segment"
  | "characters_in_scene"
  | "characters_in_shot";

/** 取条目 ID（segment_id / scene_id / shot_id）。 */
export function getScriptItemId(item: ScriptItem, mode: EditorContentMode): string {
  if (mode === "narration") return (item as NarrationSegment).segment_id;
  if (mode === "ad") return (item as AdShot).shot_id;
  return (item as DramaScene).scene_id;
}

/** 取该模式下条目的角色引用字段名。 */
export function charactersFieldFor(mode: EditorContentMode): CharactersField {
  if (mode === "drama") return "characters_in_scene";
  if (mode === "ad") return "characters_in_shot";
  return "characters_in_segment";
}
