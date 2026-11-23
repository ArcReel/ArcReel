export function hasUsableDescription(description: string | null | undefined): boolean {
  return typeof description === "string" && description.trim().length > 0;
}
