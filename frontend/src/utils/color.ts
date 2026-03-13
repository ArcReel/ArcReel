/**
 * Deterministic color assignment based on a name string.
 * Used by AvatarStack and ClueStack for fallback thumbnails.
 */

export const FALLBACK_COLORS = [
  "bg-rose-700",
  "bg-sky-700",
  "bg-emerald-700",
  "bg-amber-700",
  "bg-violet-700",
  "bg-teal-700",
  "bg-pink-700",
  "bg-indigo-700",
] as const;

export function colorForName(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) | 0;
  }
  return FALLBACK_COLORS[Math.abs(hash) % FALLBACK_COLORS.length];
}
