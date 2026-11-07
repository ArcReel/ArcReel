import { resizedSize } from "./image-resize";

/**
 * Predecode file gates match server single-source limits; reject excess without decoding.
 * Constrain source bytes, not decoded pixels; high-compression high-resolution images may still pass.
 */
export const MAX_IMAGE_FILE_BYTES = 5 * 1024 * 1024;
/** Reencoded single-image budgets match server `MAX_RESIZED_IMAGE_SOURCE_BYTES`. */
export const MAX_ENCODED_IMAGE_BYTES = (3 * 1024 * 1024) / 2;

/**
 * Always output JPEG: Safari `canvas.toBlob` / `toDataURL` lack WebP support and silently
 * fall back to PNG, larger for photographic attachments.
 */
export const TRANSCODED_IMAGE_MIME_TYPE = "image/jpeg";

/** Quality ladders start 0.85, reencoding at lower quality without pixel-size changes when budgets exceed. */
const JPEG_QUALITY_LADDER = [0.85, 0.75, 0.65, 0.55, 0.45];

export type TranscodeResult =
  | { dataUrl: string }
  /**
   * `decode`: decode errors/unavailable canvas; `oversized`: over budget even at quality floors;
   * `gif`: GIF file signatures.
   */
  | { failure: "decode" | "oversized" | "gif" };

/** Both GIF signatures. Filename-derived MIME can be spoofed by renaming; only content checks reliably block. */
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

/**
 * Decode image files, redraw by model resizing rules and encode JPEG data URLs.
 *
 * Already-valid dimensions remain unchanged, requiring JPEG reencoding only. Any failure returns
 * `failure`, requiring caller rejection, never original-image upload fallback.
 */
export async function transcodeImageToJpeg(file: File): Promise<TranscodeResult> {
  // Decode/signature checks run concurrently: filename MIME is spoofable, only signatures detect GIF;
  // unreadable signatures defer to decoding without extra failure classes. Wrap everything in try because missing browser
  // createImageBitmap throws synchronously here, otherwise permanently stalling caller transcode queues.
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
    // JPEG lacks alpha; white backgrounds prevent transparent PNG pixels becoming black.
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, width, height);
    context.drawImage(bitmap, 0, 0, width, height);
    for (const quality of JPEG_QUALITY_LADDER) {
      const dataUrl = canvas.toDataURL(TRANSCODED_IMAGE_MIME_TYPE, quality);
      // Unavailable canvas returns toDataURL placeholders like "data:,", never valid images.
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
