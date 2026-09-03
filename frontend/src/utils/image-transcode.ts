import { resizedSize } from "./image-resize";

/** 解码前的文件闸门：超过这个大小直接拒绝，避免移动端解码大图时耗尽内存。 */
export const MAX_IMAGE_FILE_BYTES = 5 * 1024 * 1024;
/** 重编码后的单张预算，与服务端 `MAX_RESIZED_IMAGE_SOURCE_BYTES` 对齐。 */
export const MAX_ENCODED_IMAGE_BYTES = (3 * 1024 * 1024) / 2;

/**
 * 输出恒为 JPEG：Safari 的 `canvas.toBlob` / `toDataURL` 不支持 WebP，且会静默
 * 回落成 PNG，PNG 对照片类附图反而更大。
 */
export const TRANSCODED_IMAGE_MIME_TYPE = "image/jpeg";

/** 质量阶梯：首档 0.85，超出单张预算时保持像素尺寸逐级降质重编码。 */
const JPEG_QUALITY_LADDER = [0.85, 0.75, 0.65, 0.55, 0.45];

export type TranscodeResult =
  | { dataUrl: string }
  /** `decode`：解码异常或 canvas 不可用；`oversized`：降到质量下限仍超出单张预算。 */
  | { failure: "decode" | "oversized" };

function dataUrlByteLength(dataUrl: string): number {
  const base64 = dataUrl.slice(dataUrl.indexOf(",") + 1);
  const padding = base64.endsWith("==") ? 2 : base64.endsWith("=") ? 1 : 0;
  return Math.floor((base64.length * 3) / 4) - padding;
}

/**
 * 把图片文件解码、按模型的缩放规则重绘、再编码成 JPEG data URL。
 *
 * 已落在模型限制内的图片不改像素尺寸，只做一次 JPEG 重编码。任何一步失败都返回
 * `failure`，调用方据此拒绝该图——不回落为原图上传。
 */
export async function transcodeImageToJpeg(file: File): Promise<TranscodeResult> {
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    return { failure: "decode" };
  }
  try {
    const [width, height] = resizedSize(bitmap.width, bitmap.height);
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) return { failure: "decode" };
    // JPEG 没有 alpha 通道：先铺白底，否则 PNG 的透明像素会被编码成黑色。
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, width, height);
    context.drawImage(bitmap, 0, 0, width, height);
    for (const quality of JPEG_QUALITY_LADDER) {
      const dataUrl = canvas.toDataURL(TRANSCODED_IMAGE_MIME_TYPE, quality);
      // canvas 不可用时 toDataURL 返回 "data:," 之类的占位串，此时不能当成图片用。
      if (!dataUrl.startsWith(`data:${TRANSCODED_IMAGE_MIME_TYPE};base64,`)) return { failure: "decode" };
      if (dataUrlByteLength(dataUrl) <= MAX_ENCODED_IMAGE_BYTES) return { dataUrl };
    }
    return { failure: "oversized" };
  } catch {
    return { failure: "decode" };
  } finally {
    bitmap.close();
  }
}
