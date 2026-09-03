import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushStream, stubSseFetch } from "@/test/fakeSseFetch";
import { openSseStream, SseStreamError, type SseMessage } from "./sse-stream";

describe("openSseStream", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

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

  it("backs off exponentially across consecutive failures and resets after a successful connection", async () => {
    const fake = stubSseFetch((index) => (index < 3 ? 503 : 200));
    const handle = openSseStream({ url: "/api/v1/stream", onMessage: () => {} });
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

    // 第四次连接成功后退避归零：再断线只等首个延迟。
    fake.latest.end();
    await flushStream();
    await vi.advanceTimersByTimeAsync(1000);
    await flushStream();
    expect(fake.connections).toHaveLength(5);
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

  it("stops permanently when the server rejects the request", async () => {
    const fake = stubSseFetch(401);
    const onError = vi.fn();
    const handle = openSseStream({ url: "/api/v1/stream", onMessage: () => {}, onError });
    await flushStream();

    expect(onError).toHaveBeenCalledTimes(1);
    const error = onError.mock.calls[0][0] as SseStreamError;
    expect(error.status).toBe(401);
    expect(error.retryable).toBe(false);
    expect(handle.closed).toBe(true);

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
