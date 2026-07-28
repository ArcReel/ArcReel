type VoidPromiseOptions = {
  onError?: (err: unknown) => void;
};

/** 把返回 Promise 的函数包装成 fire-and-forget 回调：丢弃返回值、拒绝走 onError
 *  （默认 console.error），换取可安全传给同步事件处理器的 `() => void` 签名。
 *  包装后的函数立即返回、不等待 Promise 结算——调用方若靠 await 回调维持时序
 *  （如保存中状态、串行提交），不能用本函数；只想丢弃返回值时，自写
 *  `async (...args) => { await fn(...args); }` 保留等待语义。 */
export function voidPromise<Args extends unknown[]>(
  fn: (...args: Args) => Promise<unknown>,
  opts?: VoidPromiseOptions,
): (...args: Args) => void {
  return (...args) => {
    fn(...args).catch((err: unknown) => {
      if (opts?.onError) opts.onError(err);
      else console.error(err);
    });
  };
}

export function voidCall<T>(
  promise: Promise<T>,
  onError: (err: unknown) => void = console.error,
): void {
  promise.catch(onError);
}

/** Normalize an unknown thrown value to a user-displayable string.
 *  Pass `fallback` to override the non-Error branch (e.g. an i18n message)
 *  instead of the noisy `String(e)` default. */
export function errMsg(e: unknown, fallback?: string): string {
  if (e instanceof Error) return e.message;
  return fallback ?? String(e);
}
