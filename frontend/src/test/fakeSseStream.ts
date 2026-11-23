import { vi } from "vitest";
import type { SseStreamHandle } from "@/utils/sse-stream";

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

  emit(event: string, payload: unknown): void {
    this.onEvent?.(event, payload as Record<string, unknown>);
  }
}
