import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useImageAttachments } from "./useImageAttachments";

class DeferredFileReader {
  static instances: DeferredFileReader[] = [];

  result: string | ArrayBuffer | null = null;
  onload: ((event: ProgressEvent<FileReader>) => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;

  constructor() {
    DeferredFileReader.instances.push(this);
  }

  readAsDataURL() {}

  finish(dataUrl: string) {
    this.result = dataUrl;
    this.onload?.({ target: this } as unknown as ProgressEvent<FileReader>);
  }
}

describe("useImageAttachments", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    DeferredFileReader.instances = [];
  });

  it("reports image reads as pending until the reader completes", () => {
    vi.stubGlobal("FileReader", DeferredFileReader);
    const { result } = renderHook(() => useImageAttachments());

    act(() => {
      result.current.addFiles([new File(["image"], "image.png", { type: "image/png" })]);
    });
    expect(result.current.isReading).toBe(true);

    act(() => {
      DeferredFileReader.instances[0].finish("data:image/png;base64,aW1hZ2U=");
    });
    expect(result.current.isReading).toBe(false);
    expect(result.current.images).toHaveLength(1);
  });

  it("does not let an invalidated reader change the next generation's pending state", () => {
    vi.stubGlobal("FileReader", DeferredFileReader);
    const { result } = renderHook(() => useImageAttachments());

    act(() => {
      result.current.addFiles([new File(["old"], "old.png", { type: "image/png" })]);
      result.current.resetImages();
      result.current.addFiles([new File(["new"], "new.png", { type: "image/png" })]);
    });
    expect(result.current.isReading).toBe(true);

    act(() => {
      DeferredFileReader.instances[0].finish("data:image/png;base64,b2xk");
    });
    expect(result.current.isReading).toBe(true);

    act(() => {
      DeferredFileReader.instances[1].finish("data:image/png;base64,bmV3");
    });
    expect(result.current.isReading).toBe(false);
    expect(result.current.images).toHaveLength(1);
  });

  it("reserves pending capacity across consecutive additions", () => {
    vi.stubGlobal("FileReader", DeferredFileReader);
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
    expect(DeferredFileReader.instances).toHaveLength(1);

    act(() => {
      DeferredFileReader.instances[0].finish("data:image/png;base64,Zmlyc3Q=");
    });
    expect(DeferredFileReader.instances).toHaveLength(1);
    expect(result.current.images).toHaveLength(5);
  });
});
