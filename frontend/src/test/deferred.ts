/**
 * 手动可控的 deferred promise：把一次异步调用（如 `getProject`）卡在「在途」状态，
 * 供测试精确编排多批请求的重叠时序。
 */
export function createDeferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}
