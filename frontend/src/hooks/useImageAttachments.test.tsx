import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { stubFileReader } from "@/test/fileReader";
import { useImageAttachments } from "./useImageAttachments";

describe("useImageAttachments", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reports image reads as pending until the reader completes", () => {
    const readers = stubFileReader();
    const { result } = renderHook(() => useImageAttachments());

    act(() => {
      result.current.addFiles([new File(["image"], "image.png", { type: "image/png" })]);
    });
    expect(result.current.isReading).toBe(true);

    act(() => {
      readers[0].finish("data:image/png;base64,aW1hZ2U=");
    });
    expect(result.current.isReading).toBe(false);
    expect(result.current.images).toHaveLength(1);
  });

  it("does not let an invalidated reader change the next generation's pending state", () => {
    const readers = stubFileReader();
    const { result } = renderHook(() => useImageAttachments());

    act(() => {
      result.current.addFiles([new File(["old"], "old.png", { type: "image/png" })]);
      result.current.resetImages();
      result.current.addFiles([new File(["new"], "new.png", { type: "image/png" })]);
    });
    expect(result.current.isReading).toBe(true);

    act(() => {
      readers[0].finish("data:image/png;base64,b2xk");
    });
    expect(result.current.isReading).toBe(true);

    act(() => {
      readers[1].finish("data:image/png;base64,bmV3");
    });
    expect(result.current.isReading).toBe(false);
    expect(result.current.images).toHaveLength(1);
  });

  it("reserves pending capacity across consecutive additions", () => {
    const readers = stubFileReader();
    const initialImages = Array.from({ length: 4 }, (_, index) => ({
      id: String(index),
      dataUrl: `data:image/png;base64,${index}`,
      mimeType: "image/png",
    }));
    const { result } = renderHook(() => useImageAttachments(initialImages));

    act(() => {
      result.current.addFiles([new File(["first"], "first.png", { type: "image/png" })]);
      result.current.addFiles([new File(["second"], "second.png", { type: "image/png" })]);
    });
    expect(readers).toHaveLength(1);

    act(() => {
      readers[0].finish("data:image/png;base64,Zmlyc3Q=");
    });
    expect(readers).toHaveLength(1);
    expect(result.current.images).toHaveLength(5);
  });
});
