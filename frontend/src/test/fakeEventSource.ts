import { vi } from "vitest";

/**
 * SSE 打桩的唯一替身：既可作为 `vi.stubGlobal("EventSource", …)` 的全局构造函数，
 * 也可作为 `API.openProjectEventStream` spy 的返回值。
 *
 * `instances` 按构造顺序记录本文件内建立的连接，供断言重连次数与 URL；每个用例前
 * 调用 `FakeEventSource.reset()` 清空。
 */
export class FakeEventSource {
  static instances: FakeEventSource[] = [];
  static readonly CLOSED = 2;

  static reset(): void {
    FakeEventSource.instances = [];
  }

  readyState = 0;
  onerror: ((event: Event) => void) | null = null;
  close = vi.fn(() => {
    this.readyState = FakeEventSource.CLOSED;
  });
  private readonly listeners = new Map<string, Array<(event: MessageEvent) => void>>();

  constructor(public readonly url: string = "") {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, cb: (event: MessageEvent) => void): void {
    const current = this.listeners.get(type) ?? [];
    current.push(cb);
    this.listeners.set(type, current);
  }

  /** 推送一条服务端事件，data 按真实 SSE 的形态序列化为 JSON 字符串。 */
  emit(type: string, data: unknown): void {
    const event = { data: JSON.stringify(data) } as MessageEvent;
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }
}
