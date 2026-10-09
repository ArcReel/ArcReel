import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { base64OfSize } from "@/test/image-data";
import { stubImageCanvas } from "@/test/imageCanvas";
import { MAX_ENCODED_IMAGE_BYTES } from "@/utils/image-transcode";
import { useImageAttachments } from "./useImageAttachments";

const OVER_FILE_LIMIT_BYTES = 5 * 1024 * 1024 + 1;

function imageFile(name: string, type: string, bytes = 1024): File {
  return new File([new Uint8Array(bytes)], name, { type });
}

function attachedImage(id: string, data: string, mediaType = "image/png") {
  return { id, dataUrl: `data:${mediaType};base64,${data}`, mimeType: mediaType };
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

  it("rejects a GIF whose MIME type claims it is a PNG", async () => {
    // 扩展名与 MIME 都能改名伪装，文件头不能：伪装的 GIF 解码后只剩首帧，动画被静默丢弃。
    const canvas = stubImageCanvas();
    const { result } = renderHook(() => useImageAttachments());

    act(() => {
      result.current.addFiles([
        new File([new TextEncoder().encode("GIF89a fake")], "loop.png", { type: "image/png" }),
      ]);
    });
    await act(async () => {
      await canvas.decodes[0].finish({ width: 800, height: 600 });
    });

    expect(canvas.encodes).toHaveLength(0);
    expect(result.current.images).toHaveLength(0);
    expect(result.current.error).toBe('暂不支持 GIF，图片 "loop.png" 已跳过，请转为 PNG 或 JPEG 后再上传');
    expect(result.current.isReading).toBe(false);
  });

  it("stops transcoding queued files after unmount", async () => {
    const canvas = stubImageCanvas();
    const { result, unmount } = renderHook(() => useImageAttachments());

    act(() => {
      result.current.addFiles([imageFile("a.png", "image/png"), imageFile("b.png", "image/png")]);
    });
    expect(canvas.decodes).toHaveLength(1);

    unmount();
    await act(async () => {
      await canvas.decodes[0].finish({ width: 800, height: 600 });
    });

    // 面板已经关掉：排队中的第二张不该再占着解码与内存
    expect(canvas.decodes).toHaveLength(1);
  });

  it("does not wedge the queue when the browser has no createImageBitmap", async () => {
    // 老浏览器上 createImageBitmap 未定义，调用是同步抛错；逃出转码函数就再也没人
    // 复位在途计数，附件控件会永久停在「读取中」。
    const { result } = renderHook(() => useImageAttachments());

    act(() => {
      result.current.addFiles([imageFile("shot.png", "image/png")]);
    });
    await act(async () => {});

    expect(result.current.isReading).toBe(false);
    expect(result.current.images).toHaveLength(0);
    expect(result.current.error).toBe('图片 "shot.png" 无法读取，已跳过');
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
      result.current.invalidatePendingTranscodes();
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

  it("keeps prefilled images within the encoded budget byte-for-byte", () => {
    const canvas = stubImageCanvas();
    const initialImages = [attachedImage("small", "AAAA")];
    const { result } = renderHook(() => useImageAttachments(initialImages));

    expect(result.current.isReading).toBe(false);
    expect(canvas.decodes).toHaveLength(0);
    expect(result.current.images).toEqual(initialImages);
  });

  it("transcodes a prefilled image over the encoded budget in place", async () => {
    const canvas = stubImageCanvas();
    const initialImages = [
      attachedImage("first", "AAAA"),
      attachedImage("large", base64OfSize(MAX_ENCODED_IMAGE_BYTES + 1)),
      attachedImage("last", "BBBB", "image/jpeg"),
    ];
    const { result } = renderHook(() => useImageAttachments(initialImages));

    expect(result.current.isReading).toBe(true);
    expect(canvas.decodes).toHaveLength(1);
    expect(result.current.images.map((image) => image.id)).toEqual(["first", "large", "last"]);

    await act(async () => {
      await canvas.decodes[0].finish({ width: 800, height: 600 });
    });

    expect(result.current.isReading).toBe(false);
    expect(result.current.images.map((image) => image.id)).toEqual(["first", "large", "last"]);
    expect(result.current.images[1].dataUrl.startsWith("data:image/jpeg;base64,")).toBe(true);
    expect(result.current.images[1].mimeType).toBe("image/jpeg");
  });

  it("serializes multiple prefilled transcodes", async () => {
    const canvas = stubImageCanvas();
    const initialImages = [
      attachedImage("first", base64OfSize(MAX_ENCODED_IMAGE_BYTES + 1)),
      attachedImage("second", base64OfSize(MAX_ENCODED_IMAGE_BYTES + 1)),
    ];
    const { result } = renderHook(() => useImageAttachments(initialImages));

    expect(canvas.decodes).toHaveLength(1);
    await act(async () => {
      await canvas.decodes[0].finish({ width: 800, height: 600 });
    });
    expect(canvas.decodes).toHaveLength(2);
    await act(async () => {
      await canvas.decodes[1].finish({ width: 800, height: 600 });
    });

    expect(result.current.isReading).toBe(false);
    expect(result.current.images.every((image) => image.mimeType === "image/jpeg")).toBe(true);
  });

  it("removes only the failed prefilled image and keeps the others in order", async () => {
    const canvas = stubImageCanvas();
    const initialImages = [
      attachedImage("first", "AAAA"),
      attachedImage("broken", base64OfSize(MAX_ENCODED_IMAGE_BYTES + 1)),
      attachedImage("last", "BBBB", "image/jpeg"),
    ];
    const { result } = renderHook(() => useImageAttachments(initialImages));

    await act(async () => {
      await canvas.decodes[0].fail();
    });

    expect(result.current.isReading).toBe(false);
    expect(result.current.images.map((image) => image.id)).toEqual(["first", "last"]);
    expect(result.current.error).toBe("第 2 张历史附图无法读取，已移除；如需保留请重新上传");
  });

  it("drops queued prefilled transcodes after unmount", async () => {
    const canvas = stubImageCanvas();
    const initialImages = [
      attachedImage("first", base64OfSize(MAX_ENCODED_IMAGE_BYTES + 1)),
      attachedImage("second", base64OfSize(MAX_ENCODED_IMAGE_BYTES + 1)),
    ];
    const { unmount } = renderHook(() => useImageAttachments(initialImages));

    expect(canvas.decodes).toHaveLength(1);
    unmount();
    await act(async () => {
      await canvas.decodes[0].finish({ width: 800, height: 600 });
    });

    expect(canvas.decodes).toHaveLength(1);
  });
});
