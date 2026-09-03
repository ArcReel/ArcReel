import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { stubImageCanvas } from "@/test/imageCanvas";
import { useImageAttachments } from "./useImageAttachments";

const OVER_FILE_LIMIT_BYTES = 5 * 1024 * 1024 + 1;

/** 解码成 `bytes` 字节的 base64 负载，用于驱动降质阶梯。 */
function base64OfSize(bytes: number): string {
  return "A".repeat(Math.ceil(bytes / 3) * 4);
}

function imageFile(name: string, type: string, bytes = 1024): File {
  return new File([new Uint8Array(bytes)], name, { type });
}

describe("useImageAttachments", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reports an image as pending until its transcode completes", async () => {
    const canvas = stubImageCanvas();
    const { result } = renderHook(() => useImageAttachments());

    act(() => {
      result.current.addFiles([imageFile("image.png", "image/png")]);
    });
    expect(result.current.isReading).toBe(true);

    await act(async () => {
      await canvas.decodes[0].finish({ width: 800, height: 600 });
    });
    expect(result.current.isReading).toBe(false);
    expect(result.current.images).toHaveLength(1);
  });

  it("re-encodes an oversized PNG as JPEG within the model's resize limits", async () => {
    const canvas = stubImageCanvas();
    const { result } = renderHook(() => useImageAttachments());

    act(() => {
      result.current.addFiles([imageFile("shot.png", "image/png")]);
    });
    await act(async () => {
      await canvas.decodes[0].finish({ width: 4000, height: 3000 });
    });

    expect(result.current.images[0].mimeType).toBe("image/jpeg");
    expect(result.current.images[0].dataUrl.startsWith("data:image/jpeg;base64,")).toBe(true);
    const { width, height } = canvas.encodes[0];
    expect(Math.max(width, height)).toBeLessThanOrEqual(1568);
    expect(Math.ceil(width / 28) * Math.ceil(height / 28)).toBeLessThanOrEqual(1568);
  });

  it("keeps the pixel size of an image that already fits, and still re-encodes it as JPEG", async () => {
    const canvas = stubImageCanvas();
    const { result } = renderHook(() => useImageAttachments());

    act(() => {
      result.current.addFiles([imageFile("small.jpg", "image/jpeg")]);
    });
    await act(async () => {
      await canvas.decodes[0].finish({ width: 800, height: 600 });
    });

    expect(canvas.encodes[0]).toEqual({ width: 800, height: 600, quality: 0.85 });
    expect(result.current.images[0].mimeType).toBe("image/jpeg");
  });

  it("rejects a file above the size gate before decoding it", () => {
    const canvas = stubImageCanvas();
    const { result } = renderHook(() => useImageAttachments());

    act(() => {
      result.current.addFiles([imageFile("huge.png", "image/png", OVER_FILE_LIMIT_BYTES)]);
    });

    expect(canvas.decodes).toHaveLength(0);
    expect(result.current.images).toHaveLength(0);
    expect(result.current.error).toBe('图片 "huge.png" 超过 5MB，已跳过');
  });

  it("rejects a GIF instead of uploading it unresized", () => {
    const canvas = stubImageCanvas();
    const { result } = renderHook(() => useImageAttachments());

    act(() => {
      result.current.addFiles([imageFile("loop.gif", "image/gif")]);
    });

    expect(canvas.decodes).toHaveLength(0);
    expect(result.current.images).toHaveLength(0);
    expect(result.current.error).toBe('暂不支持 GIF，图片 "loop.gif" 已跳过，请转为 PNG 或 JPEG 后再上传');
  });

  it("steps the JPEG quality down before giving up on an image that stays too large", async () => {
    const canvas = stubImageCanvas({ encodedBase64: () => base64OfSize(2 * 1024 * 1024) });
    const { result } = renderHook(() => useImageAttachments());

    act(() => {
      result.current.addFiles([imageFile("dense.png", "image/png")]);
    });
    await act(async () => {
      await canvas.decodes[0].finish({ width: 4000, height: 3000 });
    });

    expect(canvas.encodes.map((encode) => encode.quality)).toEqual([0.85, 0.75, 0.65, 0.55, 0.45]);
    expect(result.current.images).toHaveLength(0);
    expect(result.current.error).toBe('图片 "dense.png" 压缩后仍然过大，已跳过');
    expect(result.current.isReading).toBe(false);
  });

  it("accepts an image once a lower quality brings it under the budget", async () => {
    const canvas = stubImageCanvas({
      encodedBase64: (quality) => base64OfSize(quality > 0.75 ? 2 * 1024 * 1024 : 1024),
    });
    const { result } = renderHook(() => useImageAttachments());

    act(() => {
      result.current.addFiles([imageFile("dense.png", "image/png")]);
    });
    await act(async () => {
      await canvas.decodes[0].finish({ width: 4000, height: 3000 });
    });

    expect(canvas.encodes.map((encode) => encode.quality)).toEqual([0.85, 0.75]);
    expect(result.current.images).toHaveLength(1);
  });

  it("rejects an image whose decode fails instead of falling back to the original file", async () => {
    const canvas = stubImageCanvas();
    const { result } = renderHook(() => useImageAttachments());

    act(() => {
      result.current.addFiles([imageFile("broken.png", "image/png")]);
    });
    await act(async () => {
      await canvas.decodes[0].fail();
    });

    expect(result.current.images).toHaveLength(0);
    expect(result.current.error).toBe('图片 "broken.png" 无法读取，已跳过');
    expect(result.current.isReading).toBe(false);
  });

  it("rejects an image when canvas is unavailable instead of falling back to the original file", async () => {
    const canvas = stubImageCanvas();
    vi.mocked(HTMLCanvasElement.prototype.getContext).mockReturnValue(null);
    const { result } = renderHook(() => useImageAttachments());

    act(() => {
      result.current.addFiles([imageFile("unsupported.png", "image/png")]);
    });
    await act(async () => {
      await canvas.decodes[0].finish({ width: 800, height: 600 });
    });

    expect(result.current.images).toHaveLength(0);
    expect(result.current.error).toBe('图片 "unsupported.png" 无法读取，已跳过');
    expect(result.current.isReading).toBe(false);
  });

  it("does not let an invalidated transcode change the next generation's pending state", async () => {
    const canvas = stubImageCanvas();
    const { result } = renderHook(() => useImageAttachments());

    act(() => {
      result.current.addFiles([imageFile("old.png", "image/png")]);
      result.current.resetImages();
      result.current.addFiles([imageFile("new.png", "image/png")]);
    });
    expect(result.current.isReading).toBe(true);

    await act(async () => {
      await canvas.decodes[0].finish({ width: 800, height: 600 });
    });
    expect(result.current.isReading).toBe(true);

    await act(async () => {
      await canvas.decodes[1].finish({ width: 800, height: 600 });
    });
    expect(result.current.isReading).toBe(false);
    expect(result.current.images).toHaveLength(1);
  });

  it("reserves pending capacity across consecutive additions", async () => {
    const canvas = stubImageCanvas();
    const initialImages = Array.from({ length: 4 }, (_, index) => ({
      id: String(index),
      dataUrl: `data:image/png;base64,${index}`,
      mimeType: "image/png",
    }));
    const { result } = renderHook(() => useImageAttachments(initialImages));

    act(() => {
      result.current.addFiles([imageFile("first.png", "image/png")]);
      result.current.addFiles([imageFile("second.png", "image/png")]);
    });
    expect(canvas.decodes).toHaveLength(1);

    await act(async () => {
      await canvas.decodes[0].finish({ width: 800, height: 600 });
    });
    expect(canvas.decodes).toHaveLength(1);
    expect(result.current.images).toHaveLength(5);
  });
});
