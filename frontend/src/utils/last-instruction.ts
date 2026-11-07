/** Retain the last additional-instruction input per project within the session, without writing to the project; shared by the header status strip and Episodes view. */
const lastInstructions = new Map<string, string>();

export function lastInstruction(projectName: string): string {
  return lastInstructions.get(projectName) ?? "";
}

export function rememberInstruction(projectName: string, value: string): void {
  lastInstructions.set(projectName, value);
}
