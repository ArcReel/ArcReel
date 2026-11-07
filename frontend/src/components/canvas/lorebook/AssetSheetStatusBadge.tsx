/** Whether a description can be used for generation: nonempty after trimming both ends, matching the server check. */
export function hasUsableDescription(description: string | null | undefined): boolean {
  return typeof description === "string" && description.trim().length > 0;
}
