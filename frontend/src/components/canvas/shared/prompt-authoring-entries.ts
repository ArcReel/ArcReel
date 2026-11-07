/** One dialog-list entry: whether authoring is pending and whether the visual layer already has content. */
export interface PromptAuthoringEntry {
  id: string;
  pending: boolean;
  hasContent: boolean;
}

/** Array keys, ID fields and visual-layer fields for each entry shape; a reference-to-video unit's body is its visual layer. */
const ENTRY_SHAPES: readonly { key: string; idField: string; fields: readonly string[] }[] = [
  { key: "video_units", idField: "unit_id", fields: ["text"] },
  { key: "segments", idField: "segment_id", fields: ["image_prompt", "video_prompt"] },
  { key: "scenes", idField: "scene_id", fields: ["image_prompt", "video_prompt"] },
  { key: "shots", idField: "shot_id", fields: ["image_prompt", "video_prompt"] },
];

function filled(value: unknown): boolean {
  if (typeof value === "string") return value.trim().length > 0;
  if (value && typeof value === "object") return Object.keys(value).length > 0;
  return Boolean(value);
}

/** Extract the dialog list from the formal script in script order. */
export function promptAuthoringEntries(script: unknown): PromptAuthoringEntry[] {
  if (!script || typeof script !== "object") return [];
  const record = script as Record<string, unknown>;
  const shape = ENTRY_SHAPES.find((candidate) => Array.isArray(record[candidate.key]));
  if (!shape) return [];
  const items = record[shape.key] as unknown[];
  return items.flatMap((raw) => {
    if (!raw || typeof raw !== "object") return [];
    const item = raw as Record<string, unknown>;
    const id = item[shape.idField];
    if (typeof id !== "string" || !id) return [];
    return [
      {
        id,
        pending: item.pending_authoring === true,
        hasContent: shape.fields.some((field) => filled(item[field])),
      },
    ];
  });
}
