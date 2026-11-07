import { createParser } from "eventsource-parser";

import i18n from "@/i18n";

/**
 * Streaming `fetch` client for `text/event-stream`.
 *
 * Browser `EventSource` cannot carry `Authorization`; use `fetch` headers and implement
 * two native `EventSource` responsibilities: reconnect with exponential backoff and send
 * `Last-Event-ID` cursors. Refetch headers on each connection so credential updates apply naturally.
 */

export interface SseMessage {
  /** Server `event:` values; absent values mean `message`, matching browsers. */
  event: string;
  data: string;
  id?: string;
}

export class SseStreamError extends Error {
  readonly status: number | null;
  /** false means explicit server rejection, such as authentication/missing resources, without reconnection. */
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
  /** Called before each connection for additional headers, credentials/languages/etc. */
  headers?: () => HeadersInit;
  onMessage: (message: SseMessage) => void;
  /** Called once after every successful connection, including reconnections. */
  onOpen?: () => void;
  /** Called on failed/interrupted connections; `error.retryable` triggers backoff reconnects. */
  onError?: (error: SseStreamError) => void;
  /** Initial backoff milliseconds, overridable by server `retry:`. */
  initialRetryDelayMs?: number;
  maxRetryDelayMs?: number;
}

export interface SseStreamHandle {
  /** Close connections/stop reconnection, idempotently. */
  close(): void;
  readonly closed: boolean;
  /** Latest received event id, sent as `Last-Event-ID` on reconnect. */
  readonly lastEventId: string | null;
}

const DEFAULT_INITIAL_RETRY_DELAY_MS = 1000;
const DEFAULT_MAX_RETRY_DELAY_MS = 30_000;

/** Server rejection statuses that retries cannot change. */
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

  /**
   * Systems/proxies often drop background-tab connections; repeated background failures raise backoff to limits. Returning users
   * preempt remaining waits and reset backoff: foregrounding strongly signals changed network conditions, whereas tens of seconds
   * more delay retain stale pages. Connecting/connected states (empty `retryTimer`) do nothing.
   */
  const handleVisibilityChange = () => {
    if (closed || retryTimer === null) return;
    if (document.visibilityState !== "visible") return;
    clearTimeout(retryTimer);
    retryTimer = null;
    attempt = 0;
    void connect();
  };

  const visibilityHost = typeof document === "undefined" ? null : document;

  /** Shared terminal cleanup: both `close()` and nonretryable failures remove listeners, avoiding dead handles on `document`. */
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
      // EventSource rules: id in data-less blocks still updates cursors; empty id clears cursors, omitting reconnect headers.
      onId(id) {
        lastEventId = id === "" ? null : id;
      },
      onEvent(message) {
        // Receiving events proves usable connections and resets backoff. Resetting on 2xx headers alone lets
        // accept-then-disconnect proxies retain initial backoff forever, reconnecting every second.
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
        // Event callbacks may close connections; never parse subsequent chunks after closing.
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
      // Rejected response bodies are unread; cancel to release connections before retryable statuses
      // reconnect repeatedly, otherwise each unread stream holds connections until GC.
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
    // Normal server closure means disconnection/reconnect backoff, matching EventSource.
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
