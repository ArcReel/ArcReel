import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ImagePayload } from "@/types";
import { uid } from "@/utils/id";
import {
  dataUrlByteLength,
  MAX_ENCODED_IMAGE_BYTES,
  MAX_IMAGE_FILE_BYTES,
  TRANSCODED_IMAGE_MIME_TYPE,
  transcodeImageToJpeg,
  type TranscodeResult,
} from "@/utils/image-transcode";

export const MAX_ATTACHED_IMAGES = 5;

/** 转码失败原因到提示文案的映射；`gif` 与文件类型闸门共用同一条 GIF 提示。 */
const TRANSCODE_FAILURE_HINTS = {
  oversized: "image_still_too_large_hint",
  gif: "image_gif_unsupported_hint",
  decode: "image_unreadable_hint",
} as const;

const HISTORICAL_TRANSCODE_FAILURE_HINTS = {
  oversized: "message_edit_historical_attachment_oversized_hint",
  gif: "message_edit_historical_attachment_gif_hint",
  decode: "message_edit_historical_attachment_unreadable_hint",
} as const;

export interface AttachedImage {
  id: string;
  dataUrl: string;
  mimeType: string;
}

interface PendingAppendTranscode {
  kind: "append";
  file: File;
  generation: number;
}

interface InitialTranscode {
  kind: "initial";
  imageId: string;
  dataUrl: string;
  mimeType: string;
  index: number;
}

interface PendingInitialTranscode extends InitialTranscode {
  generation: number;
}

type PendingTranscode = PendingAppendTranscode | PendingInitialTranscode;

function prepareInitialImages(initialImages: AttachedImage[] | (() => AttachedImage[])) {
  const images = typeof initialImages === "function" ? initialImages() : initialImages;
  const pending: InitialTranscode[] = [];
  images.forEach((image, index) => {
    if (dataUrlByteLength(image.dataUrl) <= MAX_ENCODED_IMAGE_BYTES) return;
    pending.push({
      kind: "initial",
      imageId: image.id,
      dataUrl: image.dataUrl,
      mimeType: image.mimeType,
      index,
    });
  });
  return { images, pending };
}

function dataUrlToFile(pending: PendingInitialTranscode): File {
  const separatorIndex = pending.dataUrl.indexOf(",");
  if (separatorIndex < 0) throw new Error("invalid image data URL");
  const binary = atob(pending.dataUrl.slice(separatorIndex + 1));
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return new File([bytes], `historical-attachment-${pending.index + 1}`, { type: pending.mimeType });
}

export function imagePayloadToAttachment(image: ImagePayload): AttachedImage {
  return {
    id: uid(),
    dataUrl: `data:${image.media_type};base64,${image.data}`,
    mimeType: image.media_type,
  };
}

export function attachmentToImagePayload(image: AttachedImage): ImagePayload {
  const separatorIndex = image.dataUrl.indexOf(",");
  return {
    data: separatorIndex >= 0 ? image.dataUrl.slice(separatorIndex + 1) : "",
    media_type: image.mimeType,
  };
}

export function useImageAttachments(initialImages: AttachedImage[] | (() => AttachedImage[]) = []) {
  const { t } = useTranslation("dashboard");
  const tRef = useRef(t);
  const [initialState] = useState(() => prepareInitialImages(initialImages));
  const [images, setImages] = useState<AttachedImage[]>(initialState.images);
  const [error, setError] = useState<string | null>(null);
  const [pendingTranscodes, setPendingTranscodes] = useState(initialState.pending.length);
  const generationRef = useRef(0);
  const pendingSlotsRef = useRef(0);
  const queueRef = useRef<PendingTranscode[]>([]);
  const transcodingRef = useRef(false);
  const initialQueuedGenerationRef = useRef<number | null>(null);
  const processNextRef = useRef<() => void>(() => {});

  useEffect(() => {
    tRef.current = t;
  }, [t]);

  const processNext = useCallback(() => {
    if (transcodingRef.current) return;
    const pending = queueRef.current.shift();
    if (!pending) return;
    transcodingRef.current = true;

    void (async () => {
      let result: TranscodeResult;
      try {
        const file = pending.kind === "append" ? pending.file : dataUrlToFile(pending);
        result = await transcodeImageToJpeg(file);
      } catch {
        result = { failure: "decode" };
      }

      if (generationRef.current === pending.generation) {
        if ("dataUrl" in result) {
          if (pending.kind === "append") {
            setImages((current) => {
              if (current.length >= MAX_ATTACHED_IMAGES) return current;
              return [
                ...current,
                { id: uid(), dataUrl: result.dataUrl, mimeType: TRANSCODED_IMAGE_MIME_TYPE },
              ];
            });
          } else {
            setImages((current) => current.map((image) => (
              image.id === pending.imageId
                ? { ...image, dataUrl: result.dataUrl, mimeType: TRANSCODED_IMAGE_MIME_TYPE }
                : image
            )));
          }
        } else if (pending.kind === "append") {
          setError(tRef.current(TRANSCODE_FAILURE_HINTS[result.failure], { name: pending.file.name }));
        } else {
          setImages((current) => current.filter((image) => image.id !== pending.imageId));
          setError(tRef.current(HISTORICAL_TRANSCODE_FAILURE_HINTS[result.failure], {
            index: pending.index + 1,
          }));
        }
        if (pending.kind === "append") {
          pendingSlotsRef.current = Math.max(0, pendingSlotsRef.current - 1);
        }
        setPendingTranscodes((current) => Math.max(0, current - 1));
      }
      transcodingRef.current = false;
      processNextRef.current();
    })();
  }, []);

  useEffect(() => {
    processNextRef.current = processNext;
  }, [processNext]);

  // 初始附图超预算时，先同步进入转码态，再在挂载后把工作放进同一个串行队列。
  useEffect(() => {
    const generation = generationRef.current;
    if (initialState.pending.length > 0 && initialQueuedGenerationRef.current !== generation) {
      initialQueuedGenerationRef.current = generation;
      queueRef.current.push(...initialState.pending.map((pending) => ({ ...pending, generation })));
      processNext();
    }
    return () => {
      // 卸载后排队中的图片再解码也没人收：清空队列，别让关掉的面板继续占着解码与内存。
      generationRef.current += 1;
      queueRef.current = [];
    };
  }, [initialState.pending, processNext]);

  const addFiles = useCallback((files: File[]) => {
    setError(null);
    const generation = generationRef.current;
    const imageFiles = files.filter((file) => {
      if (!file.type.startsWith("image/")) return false;
      // GIF 不进 canvas：重编码只会留下首帧，动画内容被静默丢弃；原样上传又会让它
      // 成为唯一绕开单张预算的格式。明确拒绝，让用户自己转成 PNG / JPEG。
      if (file.type === "image/gif") {
        setError(t("image_gif_unsupported_hint", { name: file.name }));
        return false;
      }
      if (file.size <= MAX_IMAGE_FILE_BYTES) return true;
      setError(t("image_too_large_hint", { name: file.name }));
      return false;
    });
    const remainingCapacity = Math.max(
      0,
      MAX_ATTACHED_IMAGES - images.length - pendingSlotsRef.current,
    );
    if (imageFiles.length > remainingCapacity) {
      setError(t("max_images_hint", { count: MAX_ATTACHED_IMAGES }));
    }
    const filesToTranscode = imageFiles.slice(0, remainingCapacity);
    pendingSlotsRef.current += filesToTranscode.length;
    setPendingTranscodes((current) => current + filesToTranscode.length);
    queueRef.current.push(...filesToTranscode.map((file) => ({ kind: "append" as const, file, generation })));

    // 逐张串行：同时解码多张大图会在移动端撑爆内存。
    processNext();
  }, [images.length, processNext, t]);

  // 退回输入框的图片已经过发送时的转码，原样追加；超过上限也全部保留，由用户自己挑掉多出的
  const appendImages = useCallback((appended: AttachedImage[]) => {
    if (appended.length > 0) setImages((current) => [...current, ...appended]);
  }, []);

  const removeImage = useCallback((id: string) => {
    setImages((current) => current.filter((image) => image.id !== id));
    setError(null);
  }, []);

  // 发送受理后移除已发出的图片；发送途中追加的图片不在其中，保留
  const removeImages = useCallback((ids: string[]) => {
    const removed = new Set(ids);
    setImages((current) => current.filter((image) => !removed.has(image.id)));
    setError(null);
  }, []);

  const invalidatePendingTranscodes = useCallback(() => {
    generationRef.current += 1;
    setPendingTranscodes(0);
    pendingSlotsRef.current = 0;
    queueRef.current = [];
  }, []);

  return {
    images,
    error,
    isReading: pendingTranscodes > 0,
    addFiles,
    appendImages,
    removeImage,
    removeImages,
    invalidatePendingTranscodes,
  };
}
