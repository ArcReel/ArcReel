import { vi } from "vitest";

/**
 * 可控的 `text/event-stream` fetch 响应替身：把全局 `fetch` 打桩为返回一个由测试驱动的
 * `ReadableStream` 响应，供 `openSseStream` 及其上层封装的测试推送事件、结束或中断流。
 *
 * `connections` 按建连顺序记录每次 fetch 调用，用于断言 URL、请求头（`Authorization`、
 * `Last-Event-ID`）与重连次数。
 */
/**
 * 消费方的进展计数：每建一次连接、每向流索要一次下一块数据（`pull`）各记一次。
 *
 * `pull` 是真实的握手信号——消费方只有把上一块读走、喂给解析器、回到 `read()` 才会再索要，
 * 所以「不再有新的 tick」等价于「已写入的内容都读完解析完了」。
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
 * 让流式读取与解析跑完，事件回调在此之后可断言。
 *
 * 终止条件是消费方停止索要数据（一整轮让出都没有新的 `pull` 或新连接），不是固定圈数——
 * 圈数是对 fetch → reader → parser 调用链长度的猜测，链路多一跳或调度稍慢就会提前返回，
 * 让断言撞上尚未派发的事件。只要消费方还在推进就继续等，流结束或建连被拒时无新进展即返回。
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
