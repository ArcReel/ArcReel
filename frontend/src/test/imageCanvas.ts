import { vi } from "vitest";
import { createDeferred } from "./deferred";

/** 一次 `canvas.toDataURL` 调用：画布尺寸即重编码后的像素尺寸。 */
export interface EncodeCall {
  width: number;
  height: number;
  quality: number;
}

/** 一次在途的 `createImageBitmap` 解码，完成时机由测试决定。 */
export interface PendingDecode {
  readonly file: File;
  /** 以给定像素尺寸完成解码，并把附件流程推进到静止。 */
  finish(size: { width: number; height: number }): Promise<void>;
  /** 让浏览器解码这张图失败（损坏文件、不支持的编码）。 */
  fail(): Promise<void>;
}

export interface ImageCanvasStub {
  /** 按发起顺序收集的解码请求。 */
  readonly decodes: PendingDecode[];
  /** 按发起顺序收集的重编码调用。 */
  readonly encodes: EncodeCall[];
}

interface StubOptions {
  /** 每档质量编码出的 base64 负载；长度即字节预算的判据。默认为一段固定短串。 */
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
