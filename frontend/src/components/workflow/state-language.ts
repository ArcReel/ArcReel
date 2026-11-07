/**
 * Workflow-panel status tones. Colors distinguish severity only within the same axis: tasks (outlined pills) use the accent color while running,
 * terminal failures use the danger color, and other states are neutral. Unregistered status words receive no color and must never crash the entire panel during lookup.
 */
export function taskToneClass(status: string): string {
  if (status === "queued" || status === "running" || status === "succeeded") return "border-primary/30 text-primary";
  if (status === "failed" || status === "interrupted") return "border-destructive/40 text-destructive";
  return "border-input text-muted-foreground";
}
