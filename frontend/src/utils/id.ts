/** Generate a unique ID without requiring a secure context. */
export function uid(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
