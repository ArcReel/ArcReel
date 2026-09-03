import { vi } from "vitest";

/**
 * `FileReader` 的可控替身：一次读取何时完成、读出什么，都由测试说了算。
 *
 * jsdom 的真 `FileReader` 会真读一遍 Blob，完成时机随机器负载浮动；断言附件缩略图
 * 出现的用例若靠 `waitFor` 等它，并发跑整套质量门时会撞上默认超时而误红。
 */
export class DeferredFileReader {
  result: string | ArrayBuffer | null = null;
  onload: ((event: ProgressEvent<FileReader>) => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;

  readAsDataURL() {}

  /** 以 `dataUrl` 完成这次读取，同步触发 `onload`。不调用则这次读取一直在途。 */
  finish(dataUrl: string) {
    this.result = dataUrl;
    this.onload?.({ target: this } as unknown as ProgressEvent<FileReader>);
  }
}

/**
 * 把全局 `FileReader` 换成 {@link DeferredFileReader}，返回按构造顺序收集实例的数组。
 * 调用方在 `afterEach` 里 `vi.unstubAllGlobals()` 还原。
 */
export function stubFileReader(): DeferredFileReader[] {
  const readers: DeferredFileReader[] = [];
  class CollectedFileReader extends DeferredFileReader {
    constructor() {
      super();
      readers.push(this);
    }
  }
  vi.stubGlobal("FileReader", CollectedFileReader);
  return readers;
}
