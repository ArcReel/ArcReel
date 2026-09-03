import { createParser } from "eventsource-parser";

/**
 * 以 `fetch` 消费 `text/event-stream` 的流式客户端。
 *
 * 浏览器原生 `EventSource` 带不了 `Authorization` header，这里用 `fetch` 自带 header 建连，
 * 并自行承担 `EventSource` 原本提供的两件事：断线后按指数退避自动重建，重建时携带
 * `Last-Event-ID` 让服务端从游标续传。每次重建都重新取一遍 headers，凭证刷新后自然生效。
 */

export interface SseMessage {
  /** 服务端 `event:` 字段；未声明时与浏览器行为一致，视为 `message`。 */
  event: string;
  data: string;
  id?: string;
}

export class SseStreamError extends Error {
  readonly status: number | null;
  /** false 表示服务端明确拒绝（认证失败、资源不存在等），客户端不再重连。 */
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
  /** 每次建连前调用，返回附加的请求头（凭证、语言等）。 */
  headers?: () => HeadersInit;
  onMessage: (message: SseMessage) => void;
  /** 每次成功建立连接（含重连）后调用一次。 */
  onOpen?: () => void;
  /** 连接失败或中断时调用；`error.retryable` 为 true 时随后按退避重连。 */
  onError?: (error: SseStreamError) => void;
  /** 首次退避延迟（毫秒），服务端 `retry:` 字段可覆盖。 */
  initialRetryDelayMs?: number;
  maxRetryDelayMs?: number;
}

export interface SseStreamHandle {
  /** 关闭连接并停止重连；幂等。 */
  close(): void;
  readonly closed: boolean;
  /** 最近一次收到的事件 id，重连时作为 `Last-Event-ID` 发送。 */
  readonly lastEventId: string | null;
}

const DEFAULT_INITIAL_RETRY_DELAY_MS = 1000;
const DEFAULT_MAX_RETRY_DELAY_MS = 30_000;

/** 这些状态是服务端对请求本身的否定，重试不会改变结果。 */
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

  const fail = (error: SseStreamError) => {
    if (closed) return;
    options.onError?.(error);
    if (error.retryable) {
      scheduleRetry();
    } else {
      closed = true;
    }
  };

  const consume = async (body: ReadableStream<Uint8Array>, signal: AbortSignal) => {
    const parser = createParser({
      onEvent(message) {
        if (message.id !== undefined) {
          lastEventId = message.id;
        }
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
        // 连接可能在处理事件的回调里被关闭；关闭后不再解析后续分块。
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
      fail(new SseStreamError("事件流连接失败", { retryable: true, cause: error }));
      return;
    }
    if (closed) return;
    if (!response.ok || !response.body) {
      const retryable = !NON_RETRYABLE_STATUSES.has(response.status);
      fail(new SseStreamError(`事件流被拒绝: HTTP ${response.status}`, { status: response.status, retryable }));
      return;
    }

    attempt = 0;
    options.onOpen?.();
    try {
      await consume(response.body, signal);
    } catch (error) {
      if (closed || isAbortError(error)) return;
      fail(new SseStreamError("事件流中断", { retryable: true, cause: error }));
      return;
    }
    if (closed) return;
    // 服务端正常关流：与 EventSource 一致，视为断线并按退避重建。
    fail(new SseStreamError("事件流已结束", { retryable: true }));
  };

  void connect();

  return {
    close() {
      if (closed) return;
      closed = true;
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
