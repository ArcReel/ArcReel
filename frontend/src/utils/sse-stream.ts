import { createParser } from "eventsource-parser";

import i18n from "@/i18n";

export interface SseMessage {
  event: string;
  data: string;
  id?: string;
}

export class SseStreamError extends Error {
  readonly status: number | null;
  readonly retryable: boolean;

  constructor(message: string, options: { status?: number | null; retryable: boolean; cause?: unknown }) {
    super(message, { cause: options.cause });
    this.name = "SseStreamError";
    this.status = options.status ?? null;
    this.retryable = options.retryable;
  }
}

export interface SseStreamOptions {
  url: string;
  headers?: () => HeadersInit;
  onMessage: (message: SseMessage) => void;
  onOpen?: () => void;
  onError?: (error: SseStreamError) => void;
  initialRetryDelayMs?: number;
  maxRetryDelayMs?: number;
}

export interface SseStreamHandle {
  close(): void;
  readonly closed: boolean;
  readonly lastEventId: string | null;
}

const DEFAULT_INITIAL_RETRY_DELAY_MS = 1000;
const DEFAULT_MAX_RETRY_DELAY_MS = 30_000;

const NON_RETRYABLE_STATUSES = new Set([400, 401, 403, 404, 410]);

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

export function openSseStream(options: SseStreamOptions): SseStreamHandle {
  const initialRetryDelayMs = options.initialRetryDelayMs ?? DEFAULT_INITIAL_RETRY_DELAY_MS;
  const maxRetryDelayMs = options.maxRetryDelayMs ?? DEFAULT_MAX_RETRY_DELAY_MS;

  let closed = false;
  let lastEventId: string | null = null;
  let retryDelayMs = initialRetryDelayMs;
  let attempt = 0;
  let controller: AbortController | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;

  const buildHeaders = (): Headers => {
    const headers = new Headers(options.headers?.());
    headers.set("Accept", "text/event-stream");
    headers.set("Cache-Control", "no-store");
    if (lastEventId !== null) {
      headers.set("Last-Event-ID", lastEventId);
    }
    return headers;
  };

  const scheduleRetry = () => {
    if (closed) return;
    const delay = Math.min(retryDelayMs * 2 ** attempt, maxRetryDelayMs);
    attempt += 1;
    retryTimer = setTimeout(() => {
      retryTimer = null;
      void connect();
    }, delay);
  };

  const handleVisibilityChange = () => {
    if (closed || retryTimer === null) return;
    if (document.visibilityState !== "visible") return;
    clearTimeout(retryTimer);
    retryTimer = null;
    attempt = 0;
    void connect();
  };

  const visibilityHost = typeof document === "undefined" ? null : document;

  const stopWatchingVisibility = () => {
    visibilityHost?.removeEventListener("visibilitychange", handleVisibilityChange);
  };

  const fail = (error: SseStreamError) => {
    if (closed) return;
    options.onError?.(error);
    if (error.retryable) {
      scheduleRetry();
    } else {
      closed = true;
      stopWatchingVisibility();
    }
  };

  const consume = async (body: ReadableStream<Uint8Array>, signal: AbortSignal) => {
    const parser = createParser({
      // 按 EventSource 规范：不带 data 的块里的 id 同样更新续传位置；空 id 清空续传位置，重连不再带头。
      onId(id) {
        lastEventId = id === "" ? null : id;
      },
      onEvent(message) {
        attempt = 0;
        options.onMessage({ event: message.event ?? "message", data: message.data, id: message.id });
      },
      onRetry(retry) {
        retryDelayMs = retry;
      },
    });
    const decoder = new TextDecoder();
    const reader = body.getReader();
    try {
      while (!signal.aborted) {
        const { value, done } = await reader.read();
        if (done) break;
        parser.feed(decoder.decode(value, { stream: true }));
        if (closed) break;
      }
    } finally {
      reader.releaseLock();
    }
  };

  const connect = async () => {
    if (closed) return;
    controller = new AbortController();
    const { signal } = controller;
    let response: Response;
    try {
      response = await fetch(options.url, { headers: buildHeaders(), signal });
    } catch (error) {
      if (closed || isAbortError(error)) return;
      fail(new SseStreamError(i18n.t("errors:event_stream_connect_failed"), { retryable: true, cause: error }));
      return;
    }
    if (closed) return;
    if (!response.ok || !response.body) {
      // 拒绝响应的响应体不会被读取，先取消释放连接：可重试状态会按退避反复重连，
      // 每次留一条未消费的流会把连接占住直到 GC。
      void response.body?.cancel().catch(() => {});
      const retryable = !NON_RETRYABLE_STATUSES.has(response.status);
      fail(new SseStreamError(i18n.t("errors:event_stream_rejected", { status: response.status }), { status: response.status, retryable }));
      return;
    }

    options.onOpen?.();
    try {
      await consume(response.body, signal);
    } catch (error) {
      if (closed || isAbortError(error)) return;
      fail(new SseStreamError(i18n.t("errors:event_stream_interrupted"), { retryable: true, cause: error }));
      return;
    }
    if (closed) return;
    fail(new SseStreamError(i18n.t("errors:event_stream_ended"), { retryable: true }));
  };

  visibilityHost?.addEventListener("visibilitychange", handleVisibilityChange);
  void connect();

  return {
    close() {
      if (closed) return;
      closed = true;
      stopWatchingVisibility();
      if (retryTimer !== null) {
        clearTimeout(retryTimer);
        retryTimer = null;
      }
      controller?.abort();
      controller = null;
    },
    get closed() {
      return closed;
    },
    get lastEventId() {
      return lastEventId;
    },
  };
}
