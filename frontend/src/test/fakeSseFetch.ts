import { vi } from "vitest";

/**
 * 可控的 `text/event-stream` fetch 响应替身：把全局 `fetch` 打桩为返回一个由测试驱动的
 * `ReadableStream` 响应，供 `openSseStream` 及其上层封装的测试推送事件、结束或中断流。
 *
 * `connections` 按建连顺序记录每次 fetch 调用，用于断言 URL、请求头（`Authorization`、
 * `Last-Event-ID`）与重连次数。
 */
export class FakeSseConnection {
  readonly url: string;
  readonly headers: Headers;
  readonly signal: AbortSignal | undefined;
  readonly response: Response;
  private controller!: ReadableStreamDefaultController<Uint8Array>;
  private readonly encoder = new TextEncoder();

  constructor(input: RequestInfo | URL, init: RequestInit | undefined, status: number) {
    this.url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    this.headers = new Headers(init?.headers);
    this.signal = init?.signal ?? undefined;
    const body = new ReadableStream<Uint8Array>({
      start: (controller) => {
        this.controller = controller;
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

  /** 写入原始 SSE 文本（可为不完整分块）。 */
  write(raw: string): void {
    this.controller.enqueue(this.encoder.encode(raw));
  }

  /** 推送一条服务端事件，data 按真实 SSE 的形态序列化为 JSON。 */
  emit(event: string, data: unknown, id?: string): void {
    const lines = [`event: ${event}`];
    if (id !== undefined) lines.push(`id: ${id}`);
    lines.push(`data: ${JSON.stringify(data)}`);
    this.write(`${lines.join("\n")}\n\n`);
  }

  /** 服务端正常关流。 */
  end(): void {
    this.controller.close();
  }

  /** 传输层中断。 */
  fail(error: unknown = new TypeError("network error")): void {
    this.controller.error(error);
  }
}

export interface FakeSseFetch {
  connections: FakeSseConnection[];
  fetchMock: ReturnType<typeof vi.fn>;
  /** 最近一次建立的连接。 */
  readonly latest: FakeSseConnection;
}

/**
 * 用 `vi.stubGlobal("fetch", …)` 安装替身；`status` 决定每次连接的响应状态（可按调用序号变化）。
 * 由 vitest 的 `unstubAllGlobals` 或用例自行恢复。
 */
export function stubSseFetch(status: number | ((index: number) => number) = 200): FakeSseFetch {
  const connections: FakeSseConnection[] = [];
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const index = connections.length;
    const connection = new FakeSseConnection(input, init, typeof status === "function" ? status(index) : status);
    connections.push(connection);
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

/** 让流式读取与解析的微任务跑完，事件回调在此之后可断言。 */
export async function flushStream(): Promise<void> {
  for (let i = 0; i < 8; i += 1) {
    await Promise.resolve();
  }
}
