import { vi } from "vitest";

/**
 * Controlled `text/event-stream` fetch stubs: replace global `fetch` with test-driven
 * `ReadableStream` responses, letting `openSseStream` and higher-layer tests push events, end or interrupt streams.
 *
 * `connections` records fetch calls in connection order for asserting URLs, headers (`Authorization`,
 * `Last-Event-ID`) and reconnect counts.
 */
/**
 * Consumer-progress counters: count each connection and each request for the next stream chunk (`pull`).
 *
 * `pull` is a real handshake: consumers ask again only after reading previous chunks, feeding parsers and returning to `read()`;
 * therefore no new ticks means all written contents have been read and parsed.
 */
class StreamActivity {
  ticks = 0;

  tick(): void {
    this.ticks += 1;
  }
}

let activity = new StreamActivity();

export class FakeSseConnection {
  readonly url: string;
  readonly headers: Headers;
  readonly signal: AbortSignal | undefined;
  readonly response: Response;
  private controller!: ReadableStreamDefaultController<Uint8Array>;
  private readonly encoder = new TextEncoder();

  constructor(input: RequestInfo | URL, init: RequestInit | undefined, status: number, progress: StreamActivity) {
    this.url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    this.headers = new Headers(init?.headers);
    this.signal = init?.signal ?? undefined;
    const body = new ReadableStream<Uint8Array>({
      start: (controller) => {
        this.controller = controller;
      },
      pull: () => {
        progress.tick();
      },
    });
    this.response = new Response(status >= 200 && status < 300 ? body : null, {
      status,
      headers: { "Content-Type": "text/event-stream" },
    });
  }

  get aborted(): boolean {
    return this.signal?.aborted ?? false;
  }

  /** Write raw SSE text, potentially incomplete chunks. */
  write(raw: string): void {
    this.controller.enqueue(this.encoder.encode(raw));
  }

  /** Push server events, serializing data as JSON in real SSE shape. */
  emit(event: string, data: unknown, id?: string): void {
    const lines = [`event: ${event}`];
    if (id !== undefined) lines.push(`id: ${id}`);
    lines.push(`data: ${JSON.stringify(data)}`);
    this.write(`${lines.join("\n")}\n\n`);
  }

  /** Servers close streams normally. */
  end(): void {
    this.controller.close();
  }

  /** Transport interruption. */
  fail(error: unknown = new TypeError("network error")): void {
    this.controller.error(error);
  }
}

export interface FakeSseFetch {
  connections: FakeSseConnection[];
  fetchMock: ReturnType<typeof vi.fn>;
  /** Most recently established connections. */
  readonly latest: FakeSseConnection;
}

/**
 * Install through `vi.stubGlobal("fetch", …)`; `status` determines connection response statuses, potentially by call index.
 * Restore through vitest `unstubAllGlobals` or individual cases.
 */
export function stubSseFetch(status: number | ((index: number) => number) = 200): FakeSseFetch {
  const connections: FakeSseConnection[] = [];
  activity = new StreamActivity();
  const progress = activity;
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const index = connections.length;
    const resolved = typeof status === "function" ? status(index) : status;
    const connection = new FakeSseConnection(input, init, resolved, progress);
    connections.push(connection);
    progress.tick();
    return Promise.resolve(connection.response);
  });
  vi.stubGlobal("fetch", fetchMock);
  return {
    connections,
    fetchMock,
    get latest() {
      return connections[connections.length - 1];
    },
  };
}

/**
 * Finish streaming reads/parsing before asserting event callbacks.
 *
 * Stop when consumers cease requesting data (an entire yield round without new `pull` or connections), never after fixed round counts:
 * counts guess fetch → reader → parser chain lengths; extra hops/slower scheduling can return prematurely,
 * racing assertions against undispatched events. Wait while consumers advance; return without progress after stream completion/rejected connections.
 */
export async function flushStream(): Promise<void> {
  let seen = -1;
  while (seen !== activity.ticks) {
    seen = activity.ticks;
    for (let i = 0; i < 8; i += 1) {
      await Promise.resolve();
    }
  }
}
