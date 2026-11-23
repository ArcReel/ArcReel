import { resizedSize } from "./image-resize";

export const MAX_IMAGE_FILE_BYTES = 5 * 1024 * 1024;
export const MAX_ENCODED_IMAGE_BYTES = (3 * 1024 * 1024) / 2;

export const TRANSCODED_IMAGE_MIME_TYPE = "image/jpeg";

const JPEG_QUALITY_LADDER = [0.85, 0.75, 0.65, 0.55, 0.45];

export type TranscodeResult =
  | { dataUrl: string }
  | { failure: "decode" | "oversized" | "gif" };

const GIF_SIGNATURES = ["GIF87a", "GIF89a"];

async function looksLikeGif(file: File): Promise<boolean> {
  const header = new Uint8Array(await file.slice(0, 6).arrayBuffer());
  const signature = String.fromCharCode(...header);
  return GIF_SIGNATURES.includes(signature);
}

export function dataUrlByteLength(dataUrl: string): number {
  const base64 = dataUrl.slice(dataUrl.indexOf(",") + 1);
  const padding = base64.endsWith("==") ? 2 : base64.endsWith("=") ? 1 : 0;
  return Math.floor((base64.length * 3) / 4) - padding;
}

export async function transcodeImageToJpeg(file: File): Promise<TranscodeResult> {
  // 读不出文件头就交给解码分流，不额外多担一层失败。整段裹在 try 里，因为浏览器没有
  // createImageBitmap 时这一行是同步抛错，逃出去会让调用方的转码队列永远卡住。
  let isGif: boolean;
  let bitmap: ImageBitmap;
  try {
    [isGif, bitmap] = await Promise.all([
      looksLikeGif(file).catch(() => false),
      createImageBitmap(file, { imageOrientation: "from-image" }),
    ]);
  } catch {
    return { failure: "decode" };
  }
  if (isGif) {
    bitmap.close();
    return { failure: "gif" };
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
