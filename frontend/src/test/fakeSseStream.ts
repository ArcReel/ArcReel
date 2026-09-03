import { vi } from "vitest";
import type { SseStreamHandle } from "@/utils/sse-stream";

/**
 * `API.openProjectEventStream` / `API.openAssistantEntriesStream` spy 返回的流句柄替身：
 * hook 测试直接驱动订阅时注册的回调，不经真实 fetch 流（真实流式客户端见
 * `src/test/fakeSseFetch.ts`）。
 *
 * `instances` 按建连顺序记录本文件内建立的句柄，供断言建连次数与 close；每个用例前
 * 调用 `FakeSseStream.reset()` 清空。
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

  /** 推送一条已解析的服务端事件。 */
  emit(event: string, payload: unknown): void {
    this.onEvent?.(event, payload as Record<string, unknown>);
  }
}
