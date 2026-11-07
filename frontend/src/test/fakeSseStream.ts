import { vi } from "vitest";
import type { SseStreamHandle } from "@/utils/sse-stream";

/**
 * Stream-handle fakes returned by `API.openProjectEventStream` / `API.openAssistantEntriesStream` spies:
 * hook tests drive registered subscription callbacks directly, without real fetch streams (real streaming clients use
 * `src/test/fakeSseFetch.ts`).
 *
 * `instances` records handles in connection order for count/close assertions; invoke
 * `FakeSseStream.reset()` before every test.
 */
export class FakeSseStream implements SseStreamHandle {
  static instances: FakeSseStream[] = [];
  static reset(): void {
    FakeSseStream.instances = [];
  }

  closed = false;
  readonly lastEventId: string | null = null;
  readonly close = vi.fn(() => {
    this.closed = true;
  });

  constructor(private readonly onEvent?: (event: string, payload: Record<string, unknown>) => void) {
    FakeSseStream.instances.push(this);
  }

  /** Push parsed server events. */
  emit(event: string, payload: unknown): void {
    this.onEvent?.(event, payload as Record<string, unknown>);
  }
}
