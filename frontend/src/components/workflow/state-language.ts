export function taskToneClass(status: string): string {
  if (status === "queued" || status === "running" || status === "succeeded") return "border-primary/30 text-primary";
  if (status === "failed" || status === "interrupted") return "border-destructive/40 text-destructive";
  return "border-input text-muted-foreground";
}
