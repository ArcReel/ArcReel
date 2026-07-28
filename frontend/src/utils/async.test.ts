import { describe, expect, it, vi } from "vitest";
import { voidCall, voidPromise } from "./async";

describe("voidPromise", () => {
  it("returns a void function that calls fn with args", () => {
    const fn = vi.fn(async (a: number, b: string) => `${a}-${b}`);
    const wrapped = voidPromise(fn);
    const result = wrapped(1, "x");
    expect(result).toBeUndefined();
    expect(fn).toHaveBeenCalledWith(1, "x");
  });

  it("routes rejection to console.error by default", async () => {
    const err = new Error("boom");
    const fn = vi.fn(async () => { throw err; });
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    voidPromise(fn)();
    await new Promise((r) => setTimeout(r, 0));
    expect(spy).toHaveBeenCalledWith(err);
    spy.mockRestore();
  });

  it("routes rejection to custom onError when provided", async () => {
    const err = new Error("boom");
    const fn = vi.fn(async () => { throw err; });
    const onError = vi.fn();
    voidPromise(fn, { onError })();
    await new Promise((r) => setTimeout(r, 0));
    expect(onError).toHaveBeenCalledWith(err);
  });
});

describe("voidCall", () => {
  it("swallows rejection with console.error by default", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    voidCall(Promise.reject(new Error("x")));
    await new Promise((r) => setTimeout(r, 0));
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });

  it("routes rejection to custom onError", async () => {
    const onError = vi.fn();
    voidCall(Promise.reject(new Error("x")), onError);
    await new Promise((r) => setTimeout(r, 0));
    expect(onError).toHaveBeenCalled();
  });
});
