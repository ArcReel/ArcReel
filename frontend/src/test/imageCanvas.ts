import { vi } from "vitest";
import { createDeferred } from "./deferred";

export interface EncodeCall {
  width: number;
  height: number;
  quality: number;
}

export interface PendingDecode {
  readonly file: File;
  finish(size: { width: number; height: number }): Promise<void>;
  fail(): Promise<void>;
}

export interface ImageCanvasStub {
  readonly decodes: PendingDecode[];
  readonly encodes: EncodeCall[];
}

interface StubOptions {
  encodedBase64?: (quality: number) => string;
}

const DEFAULT_ENCODED_BASE64 = "anBlZw==";

async function flushMicrotasks(): Promise<void> {
  for (let turn = 0; turn < 8; turn += 1) await Promise.resolve();
}

/**
 * 把浏览器的图片解码与 canvas 编码换成可控替身，返回记录调用的句柄。
 *
 * jsdom 既没有 `createImageBitmap`，`canvas.getContext("2d")` 也恒为 null，
 * 附件流程在真实实现下根本走不完。替身只顶掉这两个浏览器边界，缩放尺寸仍由
 * 生产代码的 `resizedSize` 算出并落在 `encodes` 上。
 *
 * 调用方在 `afterEach` 里 `vi.unstubAllGlobals()` 还原。
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
