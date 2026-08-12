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
});
