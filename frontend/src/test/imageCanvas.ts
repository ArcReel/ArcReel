import { vi } from "vitest";
import { createDeferred } from "./deferred";

/** One `canvas.toDataURL` call; canvas sizes are re-encoded pixel dimensions. */
export interface EncodeCall {
  width: number;
  height: number;
  quality: number;
}

/** One pending `createImageBitmap` decode; tests determine completion times. */
export interface PendingDecode {
  readonly file: File;
  /** Complete decoding at given pixel sizes, advancing attachment flows to quiescence. */
  finish(size: { width: number; height: number }): Promise<void>;
  /** Fail browser image decoding (corruption/unsupported encoding). */
  fail(): Promise<void>;
}

export interface ImageCanvasStub {
  /** Decode requests collected in initiation order. */
  readonly decodes: PendingDecode[];
  /** Re-encoding calls collected in initiation order. */
  readonly encodes: EncodeCall[];
}

interface StubOptions {
  /** Per-quality base64 payloads; lengths determine byte budgets. Defaults to fixed short strings. */
  encodedBase64?: (quality: number) => string;
}

const DEFAULT_ENCODED_BASE64 = "anBlZw==";

async function flushMicrotasks(): Promise<void> {
  for (let turn = 0; turn < 8; turn += 1) await Promise.resolve();
}

/**
 * Replace browser image decoding/canvas encoding with controlled stubs, returning call-recording handles.
 *
 * jsdom lacks `createImageBitmap` and always returns null from `canvas.getContext("2d")`,
 * so attachment flows cannot finish through real implementations. Stub only these browser boundaries; production
 * `resizedSize` still computes resized dimensions recorded in `encodes`.
 *
 * Callers restore through `vi.unstubAllGlobals()` in `afterEach`.
 */
export function stubImageCanvas(options: StubOptions = {}): ImageCanvasStub {
  const encodedBase64 = options.encodedBase64 ?? (() => DEFAULT_ENCODED_BASE64);
  const decodes: PendingDecode[] = [];
  const encodes: EncodeCall[] = [];

  vi.stubGlobal("createImageBitmap", (file: File) => {
    const deferred = createDeferred<ImageBitmap>();
    decodes.push({
      file,
      async finish(size) {
        deferred.resolve({ ...size, close: () => {} });
        await flushMicrotasks();
      },
      async fail() {
        deferred.reject(new Error("decode failed"));
        await flushMicrotasks();
      },
    });
    return deferred.promise;
  });

  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
    fillStyle: "",
    fillRect: () => {},
    drawImage: () => {},
  } as unknown as CanvasRenderingContext2D);

  vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockImplementation(function (
    this: HTMLCanvasElement,
    _type?: string,
    quality?: unknown,
  ) {
    const encodeQuality = typeof quality === "number" ? quality : 1;
    encodes.push({ width: this.width, height: this.height, quality: encodeQuality });
    return `data:image/jpeg;base64,${encodedBase64(encodeQuality)}`;
  });

  return { decodes, encodes };
}
