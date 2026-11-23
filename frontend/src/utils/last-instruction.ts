const lastInstructions = new Map<string, string>();

export function lastInstruction(projectName: string): string {
  return lastInstructions.get(projectName) ?? "";
}

export function rememberInstruction(projectName: string, value: string): void {
  lastInstructions.set(projectName, value);
}
