import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushStream, stubSseFetch } from "@/test/fakeSseFetch";
import { openSseStream, SseStreamError, type SseMessage } from "./sse-stream";

describe("openSseStream", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    Reflect.deleteProperty(document, "visibilityState");
  });

  /** 覆盖 jsdom 的只读 `document.visibilityState`；afterEach 删除自有属性即还原。 */
  const setVisibility = (state: DocumentVisibilityState) => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  };

  it("connects with the supplied headers and dispatches parsed events", async () => {
    const fake = stubSseFetch();
    const messages: SseMessage[] = [];
    const onOpen = vi.fn();
    const handle = openSseStream({
      url: "/api/v1/stream",
      headers: () => ({ Authorization: "Bearer jwt-1" }),
      onMessage: (message) => messages.push(message),
      onOpen,
    });
    await flushStream();

    expect(fake.connections).toHaveLength(1);
    expect(fake.latest.url).toBe("/api/v1/stream");
    expect(fake.latest.headers.get("Authorization")).toBe("Bearer jwt-1");
    expect(fake.latest.headers.get("Accept")).toBe("text/event-stream");
    expect(fake.latest.headers.has("Last-Event-ID")).toBe(false);
    expect(onOpen).toHaveBeenCalledTimes(1);

    fake.latest.emit("entry", { seq: 0 }, "0");
    fake.latest.write("data: {\"plain\":true}\n\n");
    await flushStream();

    expect(messages).toEqual([
      { event: "entry", data: '{"seq":0}', id: "0" },
      { event: "message", data: '{"plain":true}', id: undefined },
    ]);
    expect(handle.lastEventId).toBe("0");
    handle.close();
  });

  it("reconnects after the server closes the stream, carrying Last-Event-ID", async () => {
    const fake = stubSseFetch();
    const onError = vi.fn();
    const onOpen = vi.fn();
    const handle = openSseStream({
      url: "/api/v1/stream",
      headers: () => ({ Authorization: "Bearer jwt-1" }),
      onMessage: () => {},
      onOpen,
      onError,
    });
    await flushStream();
    fake.latest.emit("entry", { seq: 3 }, "3");
    fake.latest.end();
    await flushStream();

    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0]).toBeInstanceOf(SseStreamError);
    expect((onError.mock.calls[0][0] as SseStreamError).retryable).toBe(true);
    expect(fake.connections).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(999);
    expect(fake.connections).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    await flushStream();

    expect(fake.connections).toHaveLength(2);
    expect(fake.latest.headers.get("Last-Event-ID")).toBe("3");
    expect(fake.latest.headers.get("Authorization")).toBe("Bearer jwt-1");
    expect(onOpen).toHaveBeenCalledTimes(2);
    expect(handle.closed).toBe(false);
    handle.close();
  });

  it("backs off exponentially across consecutive failures and resets only after an event arrives", async () => {
    const fake = stubSseFetch((index) => (index < 3 ? 503 : 200));
    const messages: SseMessage[] = [];
    const handle = openSseStream({ url: "/api/v1/stream", onMessage: (message) => messages.push(message) });
    await flushStream();
    expect(fake.connections).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(1000);
    await flushStream();
    expect(fake.connections).toHaveLength(2);

    await vi.advanceTimersByTimeAsync(1999);
    await flushStream();
    expect(fake.connections).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(1);
    await flushStream();
    expect(fake.connections).toHaveLength(3);

    await vi.advanceTimersByTimeAsync(4000);
    await flushStream();
    expect(fake.connections).toHaveLength(4);

    // 建连成功但一个事件都没送到就断流：这条连接没证明自己可用，退避继续增长到 8s，
    // 不回落到首个延迟——否则「接受连接后立刻断流」的坏代理会被按秒重连。
    fake.latest.end();
    await flushStream();
    await vi.advanceTimersByTimeAsync(7999);
    await flushStream();
    expect(fake.connections).toHaveLength(4);
    await vi.advanceTimersByTimeAsync(1);
    await flushStream();
    expect(fake.connections).toHaveLength(5);

    // 收到事件才算连接可用，退避归零：再断线只等首个延迟。
    fake.latest.emit("entry", { seq: 1 }, "1");
    await flushStream();
    expect(messages).toHaveLength(1);
    fake.latest.end();
    await flushStream();
    await vi.advanceTimersByTimeAsync(1000);
    await flushStream();
    expect(fake.connections).toHaveLength(6);
    handle.close();
  });

  it("honours the server retry field for the next reconnect delay", async () => {
    const fake = stubSseFetch();
    const handle = openSseStream({ url: "/api/v1/stream", onMessage: () => {} });
    await flushStream();
    fake.latest.write("retry: 250\n\n");
    fake.latest.end();
    await flushStream();

    await vi.advanceTimersByTimeAsync(250);
    await flushStream();
    expect(fake.connections).toHaveLength(2);
    handle.close();
  });

  it("stops permanently when the server rejects the request, releasing the visibility listener", async () => {
    const fake = stubSseFetch(401);
    const onError = vi.fn();
    // 句柄自行进入终态时调用方通常不再 close()，而 close() 对已关闭的句柄直接返回：
    // 监听只有在这条路径上就地摘掉，才不会连同整个闭包（onMessage 回调等）常驻 document。
    const addListener = vi.spyOn(document, "addEventListener");
    const removeListener = vi.spyOn(document, "removeEventListener");
    const handle = openSseStream({ url: "/api/v1/stream", onMessage: () => {}, onError });
    await flushStream();

    expect(onError).toHaveBeenCalledTimes(1);
    const error = onError.mock.calls[0][0] as SseStreamError;
    expect(error.status).toBe(401);
    expect(error.retryable).toBe(false);
    expect(handle.closed).toBe(true);

    const registered = addListener.mock.calls.find(([type]) => type === "visibilitychange");
    expect(registered).toBeDefined();
    expect(removeListener).toHaveBeenCalledWith("visibilitychange", registered![1]);

    await vi.advanceTimersByTimeAsync(60_000);
    expect(fake.connections).toHaveLength(1);
  });

  it("retries after a transport failure", async () => {
    const fake = stubSseFetch();
    const onError = vi.fn();
    const handle = openSseStream({ url: "/api/v1/stream", onMessage: () => {}, onError });
    await flushStream();
    fake.latest.fail();
    await flushStream();

    expect(onError).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1000);
    await flushStream();
    expect(fake.connections).toHaveLength(2);
    handle.close();
  });

  it("close aborts the in-flight connection and cancels a pending reconnect", async () => {
    const fake = stubSseFetch();
    const onError = vi.fn();
    const handle = openSseStream({ url: "/api/v1/stream", onMessage: () => {}, onError });
    await flushStream();

    handle.close();
    expect(handle.closed).toBe(true);
    expect(fake.latest.aborted).toBe(true);
    await flushStream();
    expect(onError).not.toHaveBeenCalled();

    // 关闭后即便流随后结束也不再重连。
    fake.latest.end();
    await flushStream();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fake.connections).toHaveLength(1);
    expect(onError).not.toHaveBeenCalled();
  });

  it("reconnects immediately and resets the backoff when the page becomes visible mid-wait", async () => {
    const fake = stubSseFetch(503);
    const handle = openSseStream({ url: "/api/v1/stream", onMessage: () => {} });
    await flushStream();
    expect(fake.connections).toHaveLength(1);

    // 连续失败把退避推到 30s 上限：1s → 2s → 4s → 8s → 16s → 30s。
    for (const delay of [1000, 2000, 4000, 8000, 16_000]) {
      await vi.advanceTimersByTimeAsync(delay);
      await flushStream();
    }
    expect(fake.connections).toHaveLength(6);

    await vi.advanceTimersByTimeAsync(29_000);
    await flushStream();
    expect(fake.connections).toHaveLength(6);

    setVisibility("visible");
    document.dispatchEvent(new Event("visibilitychange"));
    await flushStream();
    expect(fake.connections).toHaveLength(7);

    // 被抢占的 30s 定时器不再补发一次连接。
    await vi.advanceTimersByTimeAsync(1000);
    await flushStream();
    // 退避归零：这次失败只等首个延迟 1s。
    expect(fake.connections).toHaveLength(8);
    handle.close();
  });

  it("ignores visibilitychange while hidden, while connected, and after close", async () => {
    const fake = stubSseFetch((index) => (index === 0 ? 200 : 503));
    const handle = openSseStream({ url: "/api/v1/stream", onMessage: () => {} });
    await flushStream();
    expect(fake.connections).toHaveLength(1);

    // 已连接：没有待执行的重连，事件不做任何事。
    setVisibility("visible");
    document.dispatchEvent(new Event("visibilitychange"));
    await flushStream();
    expect(fake.connections).toHaveLength(1);

    fake.latest.end();
    await flushStream();

    // 退避等待中但页面仍不可见：不抢占定时器。
    setVisibility("hidden");
    document.dispatchEvent(new Event("visibilitychange"));
    await flushStream();
    expect(fake.connections).toHaveLength(1);

    // 原定时器照常到点重连。
    await vi.advanceTimersByTimeAsync(1000);
    await flushStream();
    expect(fake.connections).toHaveLength(2);

    handle.close();
    setVisibility("visible");
    document.dispatchEvent(new Event("visibilitychange"));
    await flushStream();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fake.connections).toHaveLength(2);
  });

  it("closing from inside a message handler stops parsing and reconnecting", async () => {
    const fake = stubSseFetch();
    const received: string[] = [];
    const handle = openSseStream({
      url: "/api/v1/stream",
      onMessage: (message) => {
        received.push(message.event);
        if (message.event === "status") handle.close();
      },
    });
    await flushStream();
    fake.latest.emit("status", { status: "completed" });
    fake.latest.emit("entry", { seq: 9 });
    fake.latest.end();
    await flushStream();
    await vi.advanceTimersByTimeAsync(60_000);

    expect(received).toEqual(["status"]);
    expect(fake.connections).toHaveLength(1);
  });
});
