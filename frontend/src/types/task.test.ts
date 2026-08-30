import { describe, it, expect } from "vitest";
import { isTerminalStatus } from "./task";
import type { TaskStatus } from "./task";

describe("isTerminalStatus", () => {
  it("counts succeeded/failed/cancelled as terminal", () => {
    expect(isTerminalStatus("succeeded")).toBe(true);
    expect(isTerminalStatus("failed")).toBe(true);
    expect(isTerminalStatus("cancelled")).toBe(true);
  });

  it("counts in-flight statuses as non-terminal", () => {
    const live: TaskStatus[] = ["queued", "running", "cancelling"];
    for (const status of live) expect(isTerminalStatus(status)).toBe(false);
  });
});
