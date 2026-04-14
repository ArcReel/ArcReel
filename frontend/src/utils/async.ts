type VoidPromiseOptions = {
  onError?: (err: unknown) => void;
};

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
