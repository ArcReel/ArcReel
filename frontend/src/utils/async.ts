type VoidPromiseOptions = {
  onError?: (err: unknown) => void;
};

/** Wrap a Promise-returning function as a fire-and-forget callback, discarding its return value and routing rejections to onError
 * (console.error by default), yielding a `() => void` signature safe for synchronous event handlers.
 * The wrapper returns immediately without awaiting settlement. Callers relying on awaiting callbacks for ordering,
 * such as saving state or serial submissions, must not use this helper. To discard only the return value, write
 * `async (...args) => { await fn(...args); }` to preserve awaiting semantics. */
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
